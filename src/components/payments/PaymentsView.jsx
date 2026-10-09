import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import { Download, Loader2, Plus, Receipt } from 'lucide-react';
import { APPLE_FONT, iosBtnPrimary, iosBtnSecondary, iosCard, IosSegmented } from '../ui/ios';
import PaymentRequestForm from './PaymentRequestForm';
import PaymentRequestCard from './PaymentRequestCard';
import PaymentsBoard from './PaymentsBoard';
import PaymentsDesk from './PaymentsDesk';
import PaymentsFilters from './PaymentsFilters';
import PaymentsAssets from './PaymentsAssets';
import PaymentsDictionaries from './PaymentsDictionaries';
import PaymentsRoles from './PaymentsRoles';
import {
    DOCS_STATUS_META, EMPTY_FILTERS, PAYMENT_METHOD_OPTIONS, PAYMENT_METHOD_SHORT, REGISTRY_COLUMNS, REGISTRY_COLUMNS_STORAGE_KEY,
    REQUEST_KIND_OPTIONS, STATE_FILTERS, activeFilterCount, defaultRegistryColumns, dueLabel, expenseSubline,
    exportFileName, filtersToParams, fmtDate, fmtDateShort, fmtMoney, netAmount, normalizeRegistryColumns, pageRange,
    registryTableMinWidth, registryWhere, requestState, requestType, responsibleLines, rowTone, stageLine, tonePill,
    toneRow, toneText,
} from './paymentsMeta';
import { ColumnsMenu, DueChip, NoticeBox, Pager, StatePill, TypePill, errorText, requestCardFrame } from './paymentsUi';

/*
 * Раздел «Оплата счетов» — модуль «Закуп и оплата» (ТЗ #381).
 *
 * Одна заявка «Закуп товара/услуги» идёт по этапам: инициация → согласование →
 * оплата → получение → учёт имущества → закрывающие документы → закрытие.
 * Вкладки раздела — разные виды на одни и те же заявки, копий они не создают:
 *
 *   «Мои задачи»  — рабочий стол: только то, что ждёт действия смотрящего (п. 16);
 *   доски         — «Согласование», «Бухгалтерия», «Финансовый отдел» (пп. 7–9),
 *                   каждая видна тем, кто на ней работает;
 *   «Заявки»      — реестр с поиском, фильтрами и выгрузкой в Excel;
 *   «Имущество»   — реестр поставленного на учёт (пп. 10–12);
 *   «Справочники» (в них и регулярные платежи), «Участники» — настройка процесса (п. 13).
 *
 * Набор вкладок приходит с сервера (`capabilities`): раздел рисует то, что
 * человеку положено, а не то, что следует из его должности.
 *
 * Диплинк: ?view=payments&request=<id> открывает заявку сразу — на него ведут
 * уведомления в Telegram; из колокола заявку открывает `focusRequest`.
 */

const PAGE_SIZE = 50;
const SEARCH_DEBOUNCE_MS = 300;

const requestIdFromLocation = () => {
    try {
        const value = new URLSearchParams(window.location.search).get('request');
        const id = Number(value);
        return Number.isInteger(id) && id > 0 ? id : null;
    } catch {
        return null;
    }
};

/* Заявку закрыли — её номер из адреса убираем: иначе обновление страницы
   открывало бы ту же карточку снова. */
const dropRequestFromLocation = () => {
    try {
        const url = new URL(window.location.href);
        if (!url.searchParams.has('request')) return;
        url.searchParams.delete('request');
        window.history.replaceState(window.history.state, '', `${url.pathname}${url.search}${url.hash}`);
    } catch { /* адрес не поправить — не страшно */ }
};

const optionLabel = (options, value) => options.find((option) => option.value === value)?.label || '';

const PaymentsView = ({ apiBaseUrl, withAccessTokenHeader, showToast, focusRequest = null }) => {
    const headers = useCallback(() => (withAccessTokenHeader ? withAccessTokenHeader() : {}), [withAccessTokenHeader]);

    const [ping, setPing] = useState(null);
    const [pingError, setPingError] = useState('');
    const [dictionaries, setDictionaries] = useState(null);
    const [users, setUsers] = useState([]);
    const [tab, setTab] = useState('desk');
    // Растёт после любого действия над заявкой: доски и рабочий стол перечитываются.
    const [refreshKey, setRefreshKey] = useState(0);
    const [deskCount, setDeskCount] = useState(null);

    const [items, setItems] = useState([]);
    const [total, setTotal] = useState(0);
    const [counters, setCounters] = useState({});
    const [loading, setLoading] = useState(false);
    const [loadError, setLoadError] = useState('');
    const [downloading, setDownloading] = useState(false);

    const [state, setState] = useState('all');
    const [search, setSearch] = useState('');
    const [query, setQuery] = useState('');
    const [filters, setFilters] = useState(EMPTY_FILTERS);

    const [formOpen, setFormOpen] = useState(false);
    const [editing, setEditing] = useState(null);
    const [openedId, setOpenedId] = useState(() => requestIdFromLocation());

    const toastRef = useRef(showToast);
    useEffect(() => { toastRef.current = showToast; }, [showToast]);

    const loadPing = useCallback(async () => {
        try {
            const response = await axios.get(`${apiBaseUrl}/api/payments/ping`, { headers: headers() });
            setPing(response.data);
            setDeskCount(Number(response.data?.counters?.desk) || 0);
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

    // Щелчок по уведомлению в колоколе: раздел уже открыт, нужна сама заявка.
    useEffect(() => {
        if (focusRequest?.requestId) setOpenedId(Number(focusRequest.requestId));
    }, [focusRequest]);

    useEffect(() => {
        const timer = setTimeout(() => setQuery(search.trim()), SEARCH_DEBOUNCE_MS);
        return () => clearTimeout(timer);
    }, [search]);

    const capabilities = ping?.capabilities || {};
    const meta = ping?.meta || null;
    const settings = ping?.settings || dictionaries?.settings || {};
    // За сколько дней до срока метка на карточке желтеет — та же настройка, что у напоминаний.
    const soonDays = Number(settings.due_soon_days) || undefined;
    const isAdmin = Boolean(capabilities.is_admin);
    const rolesMissing = ping?.roles_missing || [];
    const rolesReady = !rolesMissing.length;

    const tabs = useMemo(() => {
        const list = [{ value: 'desk', label: 'Мои задачи', count: deskCount ?? undefined }];
        (capabilities.boards || []).forEach((code) => {
            const board = (meta?.boards || []).find((item) => item.code === code);
            if (board) list.push({ value: `board:${code}`, label: board.short });
        });
        list.push({ value: 'requests', label: 'Заявки' });
        if (capabilities.can_view_assets) list.push({ value: 'assets', label: 'Имущество' });
        // «Регулярные платежи» — один из справочников (п. 13) и живут внутри них.
        if (isAdmin || capabilities.sees_all_requests || capabilities.can_manage_cards || capabilities.can_manage_assets) {
            list.push({ value: 'dictionaries', label: 'Справочники' });
        }
        if (isAdmin) list.push({ value: 'roles', label: 'Участники' });
        return list;
    }, [capabilities.boards, capabilities.can_view_assets, capabilities.sees_all_requests, capabilities.can_manage_cards,
        capabilities.can_manage_assets, isAdmin, meta, deskCount]);
    const activeTab = tabs.some((item) => item.value === tab) ? tab : 'desk';
    const boardCode = activeTab.startsWith('board:') ? activeTab.slice(6) : null;

    const selection = useMemo(() => {
        const params = filtersToParams(filters);
        if (state && state !== 'all') params.set('state', state);
        if (query) params.set('q', query);
        return params;
    }, [state, query, filters]);

    /* Реестр — страницами, как списки раздела «Задачи». Номер страницы помнится
       вместе с отбором, для которого его выбрали: новый отбор сразу начинается с
       первой страницы, без лишнего запроса третьей страницы прежнего отбора. */
    const selectionKey = selection.toString();
    const [paging, setPaging] = useState({ key: '', page: 1 });
    const page = paging.key === selectionKey ? paging.page : 1;
    const listRef = useRef(null);
    const goToPage = (next) => {
        setPaging({ key: selectionKey, page: next });
        // Листали стрелками под списком — показываем новую страницу с начала.
        const top = listRef.current?.getBoundingClientRect().top;
        if (top !== undefined && top < 0) listRef.current.scrollIntoView({ block: 'start', behavior: 'smooth' });
    };

    const requestRef = useRef(0);
    const load = useCallback(async (pageNumber) => {
        const ticket = requestRef.current + 1;
        requestRef.current = ticket;
        setLoading(true);
        setLoadError('');
        try {
            const params = new URLSearchParams(selectionKey);
            params.set('limit', String(PAGE_SIZE));
            params.set('offset', String((pageNumber - 1) * PAGE_SIZE));
            const response = await axios.get(`${apiBaseUrl}/api/payments/requests?${params}`, { headers: headers() });
            if (requestRef.current !== ticket) return;
            const data = response.data || {};
            setItems(data.items || []);
            setTotal(Number(data.total) || 0);
            setCounters(data.counters || {});
        } catch (error) {
            if (requestRef.current !== ticket) return;
            setLoadError(errorText(error, 'Не удалось загрузить реестр'));
        } finally {
            if (requestRef.current === ticket) setLoading(false);
        }
    }, [apiBaseUrl, headers, selectionKey]);

    // Реестр читается, когда открыт: на рабочем столе и досках он не нужен. После
    // действия над заявкой (refreshKey) перечитывается та же страница.
    useEffect(() => {
        if (activeTab !== 'requests') return;
        load(page);
    }, [activeTab, load, page, refreshKey]);

    // Страница могла уехать за конец списка (заявки закрыли, отбор сузился) — подтягиваем обратно.
    const lastPage = pageRange(page, PAGE_SIZE, total).page;
    useEffect(() => {
        if (!loading && total > 0 && lastPage !== page) setPaging({ key: selectionKey, page: lastPage });
    }, [loading, total, lastPage, page, selectionKey]);

    /* После любого действия над заявкой всё, что её показывает, перечитывается:
       состав колонок, счётчики и «мои задачи» считает сервер. Строка реестра
       сначала правится на месте, чтобы список не мигал. */
    const applyChanged = useCallback((request) => {
        if (request?.deleted) {
            setItems((prev) => prev.filter((item) => item.id !== request.id));
            setTotal((prev) => Math.max(0, prev - 1));
        } else if (request?.id) {
            setItems((prev) => prev.map((item) => (item.id === request.id ? { ...item, ...request } : item)));
        }
        setRefreshKey((prev) => prev + 1);
        loadPing();
    }, [loadPing]);

    const closeCard = useCallback(() => { setOpenedId(null); dropRequestFromLocation(); }, []);

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

    /* Набор колонок — личная настройка смотрящего, живёт в браузере. Хранилище
       может быть недоступно (приватное окно) — тогда просто набор по умолчанию. */
    const [columnKeys, setColumnKeys] = useState(() => {
        try {
            return normalizeRegistryColumns(JSON.parse(window.localStorage.getItem(REGISTRY_COLUMNS_STORAGE_KEY) || 'null'));
        } catch {
            return defaultRegistryColumns();
        }
    });
    const changeColumns = useCallback((keys) => {
        const next = normalizeRegistryColumns(keys);
        setColumnKeys(next);
        try { window.localStorage.setItem(REGISTRY_COLUMNS_STORAGE_KEY, JSON.stringify(next)); } catch { /* только в этой вкладке */ }
    }, []);
    const visibleColumns = useMemo(() => REGISTRY_COLUMNS.filter((column) => columnKeys.includes(column.key)), [columnKeys]);
    const tableMinWidth = useMemo(() => Math.max(900, registryTableMinWidth(columnKeys)), [columnKeys]);

    const renderCell = (key, request, text) => {
        const st = requestState(request);
        const active = request.status === 'active';
        const muted = (value) => <span className={text.meta}>{value}</span>;
        switch (key) {
            case 'number':
                return (
                    <>
                        <div className={`tabular-nums ${text.main}`}>№{request.id}</div>
                        <div className={`truncate text-[12px] leading-4 tabular-nums ${text.meta}`}>{fmtDateShort(request.created_at)} · {request.initiator_name || '—'}</div>
                    </>
                );
            case 'expense': {
                const sub = expenseSubline(request, columnKeys);
                return (
                    <>
                        <div className={`truncate ${text.main}`}>{request.expense_name}</div>
                        {sub && <div className={`truncate text-[12.5px] ${text.body}`}>{sub}</div>}
                    </>
                );
            }
            case 'kind': return <div className={`truncate ${text.main}`}>{optionLabel(REQUEST_KIND_OPTIONS, request.request_kind) || '—'}</div>;
            case 'entity': return request.legal_entity_name ? <div className={`truncate ${text.main}`}>{request.legal_entity_name}</div> : muted('—');
            case 'project': return request.project_name ? <div className={`truncate ${text.main}`}>{request.project_name}</div> : muted('—');
            case 'category':
                return request.category_name ? (
                    <>
                        <div className={`truncate ${text.main}`}>{request.category_name}</div>
                        {request.subcategory_name && <div className={`truncate text-[12.5px] ${text.body}`}>{request.subcategory_name}</div>}
                    </>
                ) : muted('—');
            case 'counterparty':
                return (
                    <>
                        <div className={`truncate ${text.main}`}>{request.counterparty_name || '—'}</div>
                        {request.legal_entity_name && !columnKeys.includes('entity') && (
                            <div className={`truncate text-[12.5px] ${text.body}`}>от {request.legal_entity_name}</div>
                        )}
                    </>
                );
            case 'amount':
                return (
                    <div className="whitespace-nowrap">
                        <div className={`font-medium tabular-nums ${text.main}`}>{fmtMoney(netAmount(request))}</div>
                        {request.refund_amount > 0 && <div className={`text-[12px] leading-4 tabular-nums ${text.meta}`}>возврат {fmtMoney(request.refund_amount)}</div>}
                    </div>
                );
            case 'method':
                return request.payment_method ? (
                    <>
                        <div className={`truncate ${text.main}`} title={optionLabel(PAYMENT_METHOD_OPTIONS, request.payment_method)}>{PAYMENT_METHOD_SHORT[request.payment_method]}</div>
                        {request.payment_method === 'card' && request.card_mask && (
                            <div className={`truncate text-[12px] leading-4 tabular-nums ${text.meta}`}>{request.card_mask}</div>
                        )}
                    </>
                ) : muted('—');
            case 'period': return request.payment_period ? <div className={`truncate ${text.main}`}>{request.payment_period}</div> : muted('—');
            case 'stage':
                return active ? (
                    <>
                        <div className={`truncate ${text.main}`}>{request.stage_label || '—'}</div>
                        {st === 'clarification' && <div className="truncate text-[12px] leading-4 text-amber-700">{stageLine(request).toLowerCase()}</div>}
                    </>
                ) : <StatePill request={request} />;
            case 'responsible': {
                if (!active) return muted('—');
                const who = responsibleLines(request);
                return (
                    <>
                        {/* Длинное название роли («Ответственный за учёт имущества») занимает обе строки ячейки. */}
                        <div className={`${who.sub ? 'truncate' : 'line-clamp-2 leading-snug'} ${text.main}`}>{who.name}</div>
                        {who.sub && <div className={`truncate text-[12px] leading-4 ${text.meta}`}>{who.sub}</div>}
                    </>
                );
            }
            case 'due':
                // Срок важен, пока заявка не оплачена; после оплаты и у закрытой
                // строка «к 28.09.2026» только повторяла бы дату над ней.
                if (request.due_on && active && !request.paid_on) {
                    return (
                        <div className="whitespace-nowrap">
                            <div className={`tabular-nums ${text.main}`}>{fmtDateShort(request.due_on)}</div>
                            <div className={`truncate text-[12px] leading-4 ${st === 'overdue' ? 'font-medium text-rose-700' : text.meta}`}>{dueLabel(request)}</div>
                        </div>
                    );
                }
                return request.paid_on && !columnKeys.includes('paid')
                    ? <div className={`whitespace-nowrap text-[12.5px] tabular-nums ${text.body}`}>оплачено {fmtDateShort(request.paid_on)}</div>
                    : muted('—');
            case 'paid': return request.paid_on ? <div className={`tabular-nums ${text.main}`}>{fmtDate(request.paid_on)}</div> : muted('—');
            case 'invoice':
                return request.invoice_number || request.invoice_date ? (
                    <>
                        <div className={`truncate tabular-nums ${text.main}`}>{request.invoice_number ? `№${request.invoice_number}` : '—'}</div>
                        {request.invoice_date && <div className={`text-[12px] leading-4 tabular-nums ${text.meta}`}>от {fmtDate(request.invoice_date)}</div>}
                    </>
                ) : muted('—');
            case 'docs':
                // До оплаты закрывающих документов не ждут — статус «не получены»
                // у заявки на согласовании читался бы как упрёк.
                return request.paid_on
                    ? <div className={`truncate ${text.main}`}>{(DOCS_STATUS_META[request.closing_docs_status] || DOCS_STATUS_META.none).label}</div>
                    : muted('—');
            case 'notes': return request.notes ? <div className={`line-clamp-2 text-[12.5px] ${text.body}`}>{request.notes}</div> : muted('—');
            default: return null;
        }
    };

    const filtersActive = activeFilterCount(filters);
    const openCreate = () => { setEditing(null); setFormOpen(true); };
    const narrowed = Boolean(query || filtersActive || state !== 'all');

    return (
        // На телефоне у корня свой непрозрачный фон: область раздела там прозрачна,
        // и фоновый логотип портала просвечивал бы серыми фигурами между карточками.
        <div className="mx-auto min-h-screen max-w-[1400px] bg-gray-50 px-3 pb-12 pt-3 sm:px-5 md:min-h-0" style={{ fontFamily: APPLE_FONT }}>
            <header className="flex flex-wrap items-center justify-between gap-3">
                <h1 className="min-w-0 text-[22px] font-semibold tracking-tight text-slate-900">Оплата счетов</h1>
                {capabilities.can_create && (
                    <button type="button" className={`${iosBtnPrimary} w-full whitespace-nowrap sm:w-auto`} onClick={openCreate} disabled={!rolesReady}>
                        <Plus size={15} /> Создать заявку
                    </button>
                )}
            </header>

            <div className="-mx-3 mt-4 overflow-x-auto px-3 [scrollbar-width:none] sm:mx-0 sm:px-0">
                <IosSegmented value={activeTab} options={tabs} onChange={setTab} size="lg" ariaLabel="Раздел" className="min-w-max" />
            </div>

            {pingError && <div className="mt-4"><NoticeBox text={pingError} /></div>}
            {ping && ping.schema_ready === false && (
                <div className="mt-4"><NoticeBox text="Раздел разворачивается — таблицы появятся после перезапуска сервера." /></div>
            )}
            {ping && !rolesReady && (
                <div className="mt-4">
                    <NoticeBox text={`Заявки пока не завести: не назначены участники — ${rolesMissing.join(', ')}.`}>
                        {isAdmin && (
                            <>
                                {' '}
                                <button type="button" className="font-medium underline decoration-amber-300 underline-offset-2 hover:decoration-amber-600" onClick={() => setTab('roles')}>Открыть «Участники»</button>
                            </>
                        )}
                    </NoticeBox>
                </div>
            )}
            {ping && ping.storage_ready === false && (
                <div className="mt-3"><NoticeBox tone="slate" text="Хранилище файлов не настроено: счета, чеки и акты прикрепить не получится, пока не задан бакет." /></div>
            )}
            {ping && isAdmin && ping.legacy_pending > 0 && (
                <div className="mt-3"><NoticeBox text={`Заявок прежней версии процесса не переведено на этапы: ${ping.legacy_pending}. Работать с ними нельзя, пока перенос не пройдёт, — причина записана в журнале сервера.`} /></div>
            )}
            {ping && isAdmin && ping.card_key_ready === false && (
                <div className="mt-3"><NoticeBox tone="slate" text="Не задан ключ шифрования номеров карт: заявки на пополнение карты сохранить не получится." /></div>
            )}

            {/* Вкладка не подменяется рывком: содержимое мягко проявляется (fade-in-soft). */}
            {activeTab === 'desk' && (
                <div className="mt-4 motion-safe:animate-fade-in-soft">
                    <PaymentsDesk apiBaseUrl={apiBaseUrl} headers={headers} refreshKey={refreshKey} soonDays={soonDays} onOpen={setOpenedId} onCount={setDeskCount} />
                </div>
            )}

            {boardCode && meta && (
                <div key={boardCode} className="mt-4 motion-safe:animate-fade-in-soft">
                    <PaymentsBoard
                        code={boardCode}
                        apiBaseUrl={apiBaseUrl}
                        headers={headers}
                        dictionaries={dictionaries}
                        users={users}
                        meta={meta}
                        soonDays={soonDays}
                        refreshKey={refreshKey}
                        requestOpen={Boolean(openedId) || formOpen}
                        onOpen={setOpenedId}
                        onChanged={applyChanged}
                        showToast={showToast}
                    />
                </div>
            )}

            {activeTab === 'requests' && (
                <div className="mt-4 motion-safe:animate-fade-in-soft">
                    <PaymentsFilters search={search} onSearch={setSearch} filters={filters} onChange={setFilters} dictionaries={dictionaries} users={users}>
                        <div className="hidden md:block">
                            <ColumnsMenu columns={REGISTRY_COLUMNS} value={columnKeys} onChange={changeColumns} onReset={() => changeColumns(defaultRegistryColumns())} />
                        </div>
                        <button type="button" className={`${iosBtnSecondary} shrink-0`} disabled={downloading || !items.length} onClick={exportXlsx} title="Выгрузка реестра в Excel по текущему отбору">
                            {downloading ? <Loader2 size={15} className="animate-spin" /> : <Download size={15} />}
                            <span className="hidden sm:inline">Excel</span>
                        </button>
                    </PaymentsFilters>

                    <div className="mt-3 flex flex-wrap items-center gap-1 rounded-xl bg-slate-100 px-2 py-1.5">
                        {STATE_FILTERS.map((item) => {
                            const count = counters[item.counter];
                            const active = state === item.key;
                            if (!['all', 'mine', 'open'].includes(item.key) && !count && !active) return null;
                            return (
                                <button
                                    key={item.key}
                                    type="button"
                                    data-state-filter={item.key}
                                    aria-pressed={active}
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

                    {loadError && <div className="mt-4 rounded-2xl bg-red-50 px-4 py-3 text-[13px] text-red-700 ring-1 ring-red-200">{loadError}</div>}

                    {/* Таблица на широком экране, карточки на телефоне. */}
                    <div ref={listRef} className="scroll-mt-4" />
                    <div className={`${iosCard} mt-4 hidden overflow-x-auto md:block`}>
                        <table className="w-full table-fixed border-collapse text-[13.5px]" style={{ minWidth: tableMinWidth }}>
                            <colgroup>
                                {visibleColumns.map((column) => <col key={column.key} style={column.width ? { width: column.width } : undefined} />)}
                            </colgroup>
                            <thead>
                                <tr className="border-b border-slate-200/70 bg-slate-50 text-left text-[11.5px] uppercase tracking-wider text-slate-500">
                                    {visibleColumns.map((column) => (
                                        <th key={column.key} className={`truncate px-3.5 py-2.5 font-semibold ${column.align === 'right' ? 'text-right' : ''}`}>{column.label}</th>
                                    ))}
                                </tr>
                            </thead>
                            <tbody>
                                {items.map((request, index) => {
                                    const tone = rowTone(request);
                                    return (
                                        <tr
                                            key={request.id}
                                            data-request={request.id}
                                            onClick={() => setOpenedId(request.id)}
                                            className={`h-[60px] cursor-pointer transition hover:brightness-[0.97] ${index > 0 ? 'border-t border-slate-900/[0.06]' : ''} ${toneRow(tone)}`}
                                        >
                                            {visibleColumns.map((column) => (
                                                <td key={column.key} className={`px-3.5 py-2.5 align-top ${column.align === 'right' ? 'text-right' : ''}`}>
                                                    {renderCell(column.key, request, toneText(tone))}
                                                </td>
                                            ))}
                                        </tr>
                                    );
                                })}
                            </tbody>
                        </table>
                        {!items.length && !loading && (
                            <div className="flex flex-col items-center gap-2 px-4 py-12 text-center">
                                <Receipt size={22} className="text-slate-300" />
                                <p className="text-[13.5px] text-slate-500">
                                    {narrowed ? 'Ничего не нашлось — измените запрос или фильтры' : 'Заявок пока нет'}
                                </p>
                            </div>
                        )}
                    </div>

                    {/* Карточка — та же, что на досках: тип заявки цветом (метка и полоса у левого
                        края), номер и срок, название, поставщик; за линией — сумма и где заявка
                        сейчас. Кружок у этапа — того же цвета, что у фильтра над списком. */}
                    <div className="mt-4 space-y-2 md:hidden">
                        {items.map((request) => {
                            const tone = rowTone(request);
                            const where = registryWhere(request);
                            return (
                                <button
                                    key={request.id}
                                    type="button"
                                    onClick={() => setOpenedId(request.id)}
                                    className={`${requestCardFrame(requestType(request))} block w-full py-3 pl-4 pr-3.5 text-left transition active:scale-[0.99]`}
                                >
                                    <span className="flex min-h-[20px] items-center justify-between gap-2">
                                        <span className="flex min-w-0 grow basis-0 items-center gap-2">
                                            <TypePill request={request} />
                                            <span className="shrink-0 text-[11.5px] tabular-nums text-slate-400">№{request.id}</span>
                                        </span>
                                        <DueChip request={request} soonDays={soonDays} />
                                    </span>
                                    {/* Без `block`: рядом с line-clamp он отменил бы обрезку. */}
                                    <span className="mt-1 text-[14px] font-semibold leading-snug text-slate-900 line-clamp-2">{request.expense_name}</span>
                                    {request.counterparty_name && <span className="mt-0.5 block truncate text-[12.5px] text-slate-600">{request.counterparty_name}</span>}
                                    <span className="mt-2.5 flex items-center justify-between gap-3 border-t border-slate-100 pt-2">
                                        <span className="shrink-0 text-[14px] font-semibold tabular-nums text-slate-900">{fmtMoney(netAmount(request))}</span>
                                        <span className="inline-flex min-w-0 grow basis-0 items-center justify-end gap-1.5 text-[12px] text-slate-500" title={where}>
                                            {tone && <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${tonePill(tone).dot}`} aria-hidden="true" />}
                                            <span className="truncate">{where}</span>
                                        </span>
                                    </span>
                                </button>
                            );
                        })}
                        {!items.length && !loading && (
                            <div className={`${iosCard} px-4 py-10 text-center text-[13.5px] text-slate-500`}>{narrowed ? 'Ничего не нашлось' : 'Заявок пока нет'}</div>
                        )}
                    </div>

                    {loading && !items.length && (
                        <div className="mt-4 flex items-center justify-center gap-2 text-[13px] text-slate-500"><Loader2 size={15} className="animate-spin" /> Загружаем реестр…</div>
                    )}
                    <div className="mt-3">
                        <Pager page={page} pageSize={PAGE_SIZE} total={total} loading={loading && items.length > 0} onPage={goToPage} hideSingle />
                    </div>
                </div>
            )}

            {activeTab === 'assets' && (
                <div className="mt-4 motion-safe:animate-fade-in-soft">
                    <PaymentsAssets
                        apiBaseUrl={apiBaseUrl}
                        headers={headers}
                        dictionaries={dictionaries}
                        users={users}
                        showToast={showToast}
                        onOpenRequest={setOpenedId}
                    />
                </div>
            )}

            {activeTab === 'dictionaries' && (
                <div className="mt-4 motion-safe:animate-fade-in-soft">
                    <PaymentsDictionaries
                        apiBaseUrl={apiBaseUrl}
                        headers={headers}
                        users={users}
                        dictionaries={dictionaries}
                        capabilities={capabilities}
                        me={ping?.me}
                        onDictionariesChanged={loadDictionaries}
                        onSettingsChanged={loadPing}
                        onRequestsChanged={() => applyChanged(null)}
                        onOpenRequest={setOpenedId}
                        showToast={showToast}
                    />
                </div>
            )}

            {activeTab === 'roles' && (
                <div className="mt-4 motion-safe:animate-fade-in-soft">
                    <PaymentsRoles
                        apiBaseUrl={apiBaseUrl}
                        headers={headers}
                        roles={ping?.roles || {}}
                        users={users}
                        onChanged={(roles) => { setPing((prev) => (prev ? { ...prev, roles } : prev)); loadPing(); }}
                        showToast={showToast}
                        canEdit={isAdmin}
                    />
                </div>
            )}

            <PaymentRequestForm
                open={formOpen}
                // Доработку открывают из карточки заявки — туда же и возвращаемся, а не в список.
                onClose={() => {
                    const back = editing?.request?.id;
                    setFormOpen(false);
                    setEditing(null);
                    if (back) setOpenedId(back);
                }}
                apiBaseUrl={apiBaseUrl}
                headers={headers}
                dictionaries={dictionaries}
                users={users}
                me={ping?.me}
                meta={meta}
                settings={settings}
                staffed={ping?.roles_staffed}
                data={editing}
                onSaved={(body) => {
                    applyChanged(body?.request);
                    // Новый поставщик мог появиться прямо в заявке — справочник перечитываем.
                    loadDictionaries();
                    if (body?.request?.id) setOpenedId(body.request.id);
                }}
                showToast={showToast}
            />

            <PaymentRequestCard
                open={Boolean(openedId)}
                onClose={closeCard}
                requestId={openedId}
                apiBaseUrl={apiBaseUrl}
                headers={headers}
                dictionaries={dictionaries}
                users={users}
                me={ping?.me}
                meta={meta}
                onChanged={applyChanged}
                onEdit={(data) => { setOpenedId(null); setEditing(data); setFormOpen(true); }}
                showToast={showToast}
            />
        </div>
    );
};

export default PaymentsView;
