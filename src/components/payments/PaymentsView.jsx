import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import { Download, Loader2, Plus, Receipt } from 'lucide-react';
import { APPLE_FONT, iosBtnPrimary, iosBtnSecondary, iosCard, IosSegmented, prefersReducedMotion } from '../ui/ios';
import PaymentRequestForm from './PaymentRequestForm';
import PaymentRequestCard from './PaymentRequestCard';
import PaymentsBoard from './PaymentsBoard';
import PaymentsDesk from './PaymentsDesk';
import PaymentsFilters from './PaymentsFilters';
import PaymentsAssets from './PaymentsAssets';
import PaymentsDictionaries, { dictionarySections } from './PaymentsDictionaries';
import PaymentsRoles from './PaymentsRoles';
import {
    DOCS_STATUS_META, EMPTY_FILTERS, PAYMENT_METHOD_OPTIONS, PAYMENT_METHOD_SHORT, REGISTRY_COLUMNS, REGISTRY_COLUMNS_STORAGE_KEY,
    REQUEST_KIND_OPTIONS, STATE_FILTERS, activeFilterCount, closedGroupStart, defaultRegistryColumns, dueLabel, expenseSubline,
    exportFileName, filtersToParams, fmtDate, fmtDateShort, fmtMoney, netAmount, normalizeRegistryColumns, pageRange,
    paidLabel, registryTableMinWidth, registryWhere, requestState, requestType, responsibleLines, rowTone, stageLine,
    tonePill, toneText,
} from './paymentsMeta';
import {
    Bone, CARD_ROW, CARD_TABLE, CARD_TH, ColumnsMenu, DueChip, GroupDivider, NoticeBox, Pager, SECTION_COLUMN, StatePill,
    StatTiles, StatTilesSkeleton, TypePill, cardCell, errorText, requestCardFrame,
} from './paymentsUi';
import { recall, recallShell, remember, rememberPing } from './paymentsEntry';
import { PAYMENTS_DEFAULT_TAB, readPaymentsLink, resolvePaymentsLink, syncPaymentsLink } from './paymentsLink';

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
 * человеку положено, а не то, что следует из его должности. Чтобы раздел при
 * входе не перестраивался на глазах (две вкладки → восемь), он помнит прошлый
 * ответ и первый кадр рисует им — см. paymentsEntry.js.
 *
 * Адрес повторяет открытое: вкладку, справочник, карточку имущества и заявку
 * (paymentsLink.js). По нему раздел открывается после перезагрузки и по присланной
 * ссылке; ?view=payments&request=<id> — ссылка из Telegram. Из колокола заявку
 * открывает `focusRequest`.
 */

const PAGE_SIZE = 50;
const SEARCH_DEBOUNCE_MS = 300;

// Что открыть при входе — по адресу. Нет окна (серверный рендер тестов) — пусто.
const linkAtEntry = () => readPaymentsLink(typeof window === 'undefined' ? '' : window.location?.search);

const optionLabel = (options, value) => options.find((option) => option.value === value)?.label || '';

/* Вкладка или справочник из ссылки ждут ответа сервера о правах — заготовка на их месте. */
const LinkPending = ({ kind, className = '' }) => (
    <div className={`${iosCard} space-y-3 px-4 py-4 ${className}`} role="status" aria-busy="true" aria-label="Открываем ссылку" data-link-pending={kind}>
        {['w-2/3', 'w-1/2', 'w-3/5'].map((width) => <Bone key={width} className={`h-3 ${width}`} />)}
    </div>
);

const PaymentsView = ({ apiBaseUrl, withAccessTokenHeader, showToast, focusRequest = null, userId = null }) => {
    const headers = useCallback(() => (withAccessTokenHeader ? withAccessTokenHeader() : {}), [withAccessTokenHeader]);

    /* Что раздел помнит с прошлого входа (paymentsEntry.js). `ping` — полный ответ сервера:
       свежий или из памяти страницы; `entry.shell` — его оболочка из хранилища браузера, она
       есть и сразу после перезагрузки. Первый кадр рисуется ими, запросы обновляют фоном. */
    const [entry] = useState(() => ({
        ping: recall(userId, 'ping'),
        shell: recallShell(userId),
        desk: recall(userId, 'desk'),
        // Первая страница реестра без отбора: «Заявки» открываются тем же, чем были, и обновляются фоном.
        registry: recall(userId, 'registry'),
        dictionaries: recall(userId, 'dictionaries'),
        users: recall(userId, 'users'),
    }));
    const [ping, setPing] = useState(entry.ping);
    const [pingError, setPingError] = useState('');
    // Сервер ответил на /ping в этот вход: settled — успешно или ошибкой, ok — успешно (права известны точно).
    const [pingSettled, setPingSettled] = useState(false);
    const [pingOk, setPingOk] = useState(false);
    // Метки ссылки, которую раздел не открыл из-за сбоя /ping: держатся в адресе до ответа или до щелчка.
    const [heldLink, setHeldLink] = useState(null);
    const [dictionaries, setDictionaries] = useState(entry.dictionaries);
    const [users, setUsers] = useState(entry.users || []);
    // Что открыть — по адресу (paymentsLink.js): вкладка, справочник, карточка имущества, заявка.
    const [link] = useState(linkAtEntry);
    const [tab, setTab] = useState(link.tab);
    const [dictionary, setDictionary] = useState(link.dict);
    const [assetId, setAssetId] = useState(link.asset);
    // Растёт после любого действия над заявкой: доски и рабочий стол перечитываются.
    const [refreshKey, setRefreshKey] = useState(0);
    const [deskCount, setDeskCount] = useState(() => {
        const count = entry.desk?.total ?? (entry.ping || entry.shell)?.counters?.desk;
        return count === undefined || count === null ? null : Number(count) || 0;
    });

    const [items, setItems] = useState(entry.registry?.items || []);
    const [total, setTotal] = useState(entry.registry?.total || 0);
    // null — счётчики ещё не приходили: плитки стоят заготовкой, а не тремя из восьми.
    const [counters, setCounters] = useState(entry.registry?.counters || null);
    const [loading, setLoading] = useState(false);
    const [loadError, setLoadError] = useState('');
    const [downloading, setDownloading] = useState(false);

    const [state, setState] = useState('all');
    const [search, setSearch] = useState('');
    const [query, setQuery] = useState('');
    const [filters, setFilters] = useState(EMPTY_FILTERS);

    const [formOpen, setFormOpen] = useState(false);
    const [createWanted, setCreateWanted] = useState(false);
    const [editing, setEditing] = useState(null);
    // Форму доработки открыли из задачи инициатора, чтобы дописать недостающее.
    const [revealIssues, setRevealIssues] = useState(false);
    const [openedId, setOpenedId] = useState(link.request);

    const toastRef = useRef(showToast);
    useEffect(() => { toastRef.current = showToast; }, [showToast]);

    const loadPing = useCallback(async () => {
        try {
            const response = await axios.get(`${apiBaseUrl}/api/payments/ping`, { headers: headers() });
            setPing(response.data);
            rememberPing(userId, response.data);
            setDeskCount(Number(response.data?.counters?.desk) || 0);
            setPingError('');
            setPingOk(true);
        } catch (error) {
            setPingError(errorText(error, 'Раздел недоступен'));
            setCreateWanted(false);
        } finally {
            setPingSettled(true);
        }
    }, [apiBaseUrl, headers, userId]);

    const loadDictionaries = useCallback(async () => {
        try {
            const response = await axios.get(`${apiBaseUrl}/api/payments/dictionaries`, { headers: headers() });
            setDictionaries(response.data);
            remember(userId, 'dictionaries', response.data);
        } catch { /* форма покажет пустые списки */ }
    }, [apiBaseUrl, headers, userId]);

    useEffect(() => { loadPing(); loadDictionaries(); }, [loadPing, loadDictionaries]);

    useEffect(() => {
        let cancelled = false;
        axios.get(`${apiBaseUrl}/api/payments/users`, { headers: headers() })
            .then((response) => {
                if (cancelled) return;
                const list = response.data?.users || [];
                setUsers(list);
                remember(userId, 'users', list);
            })
            .catch(() => {});
        return () => { cancelled = true; };
    }, [apiBaseUrl, headers, userId]);

    // Щелчок по уведомлению в колоколе: раздел уже открыт, нужна сама заявка.
    useEffect(() => {
        if (focusRequest?.requestId) setOpenedId(Number(focusRequest.requestId));
    }, [focusRequest]);

    useEffect(() => {
        const timer = setTimeout(() => setQuery(search.trim()), SEARCH_DEBOUNCE_MS);
        return () => clearTimeout(timer);
    }, [search]);

    // Оболочка раздела — вкладки, кнопка, предупреждения — рисуется по тому, что известно:
    // свежему ответу, памяти страницы или записи в хранилище. Ничего нет — заготовкой; сервер
    // не ответил вовсе — как раньше, тем, что есть у всех («Мои задачи» и «Заявки»).
    const known = ping || entry.shell;
    const shellKnown = Boolean(known) || Boolean(pingError);
    const capabilities = known?.capabilities || {};
    const meta = known?.meta || null;
    const settings = known?.settings || dictionaries?.settings || {};
    // За сколько дней до срока метка на карточке желтеет — та же настройка, что у напоминаний.
    const soonDays = Number(settings.due_soon_days) || undefined;
    const isAdmin = Boolean(capabilities.is_admin);
    const rolesMissing = known?.roles_missing || [];
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
    /* Что показать и что писать в адрес (paymentsLink.js::resolvePaymentsLink). Вкладка или
       справочник из ссылки, которых нет в известных правах, ждут ответа сервера: на их месте
       заготовка, а не «Мои задачи», которые через миг сменились бы ими. */
    const tabValues = useMemo(() => tabs.map((item) => item.value), [tabs]);
    const dictionaryKeys = useMemo(() => dictionarySections(capabilities).map((item) => item.value), [capabilities]);
    const {
        activeTab, tabPending, activeDictionary, dictionaryPending, missing, marks,
    } = resolvePaymentsLink({
        tab, dict: dictionary, asset: assetId, request: openedId,
        tabs: tabValues, dictionaries: dictionaryKeys, settled: pingSettled, held: heldLink,
    });
    const boardCode = activeTab.startsWith('board:') ? activeTab.slice(6) : null;

    /* Ответ пришёл, а вкладки или справочника из ссылки у человека нет — остаются «Мои задачи» и
       первый справочник. Сервер ответил ОШИБКОЙ — это не ответ «вкладки нет»: на экране то же,
       но метки ссылки остаются в адресе (heldLink), и обновление страницы откроет её снова. */
    useEffect(() => {
        if (!missing) return;
        if (!pingOk) setHeldLink((prev) => prev || { tab, dict: dictionary, asset: assetId });
        setTab((prev) => (tabValues.includes(prev) ? prev : PAYMENTS_DEFAULT_TAB));
        setDictionary((prev) => (!prev || dictionaryKeys.includes(prev) ? prev : ''));
    }, [missing, pingOk, tab, dictionary, assetId, tabValues, dictionaryKeys]);
    // Права подтвердились — адрес снова повторяет экран.
    useEffect(() => { if (pingOk) setHeldLink(null); }, [pingOk]);

    /* Другая вкладка — справочник и карточка имущества прежней закрываются, как и до адреса:
       их состояние жило внутри вкладки. Сравнение — с выбранной вкладкой, а не с показанной:
       пока вкладка из ссылки ждёт прав, щелчок по «Мои задачи» обязан сработать. */
    const changeTab = useCallback((value) => {
        if (value === tab) return;
        setTab(value);
        setDictionary('');
        setAssetId(null);
        setHeldLink(null);
    }, [tab]);
    const changeDictionary = useCallback((value) => {
        setDictionary(value);
        setHeldLink(null);
    }, []);

    /* ОТКРЫТОЕ — В АДРЕСНОЙ СТРОКЕ: вкладка, справочник, карточка имущества и заявка. Это и есть
       ссылка, которую копируют из строки браузера, и по ней же перезагрузка возвращает туда, где
       человек был (paymentsLink.js).

       «Назад» на телефоне: стек жестов (utils/mobileBackStack.js) возвращает в строку адрес из
       истории, а там бывает номер заявки, закрытой крестиком, — без поправки строка показывала бы
       заявку, окна которой на экране нет. Поэтому после каждого «назад» открытое пишется снова;
       жест, уводящий в другой раздел, адрес меняет сам, и его мы не трогаем (onlyHere). */
    const { tab: markTab, dict: markDict, asset: markAsset, request: markRequest } = marks;
    useEffect(() => {
        const current = { tab: markTab, dict: markDict, asset: markAsset, request: markRequest };
        syncPaymentsLink(current);
        const restore = () => syncPaymentsLink(current, { onlyHere: true });
        window.addEventListener('popstate', restore);
        return () => window.removeEventListener('popstate', restore);
    }, [markTab, markDict, markAsset, markRequest]);

    /* Лента вкладок бывает шире экрана (телефон, узкое окно компьютера), и выбранная
       вкладка оказывалась обрезанной краем, а следующие за ней — не видны вовсе.
       Подводим ленту к выбранной вкладке; двигаем только саму ленту. При открытии
       раздела она встаёт сразу, дальше — доезжает. */
    const tabsRef = useRef(null);
    const tabsPlaced = useRef(false);
    useEffect(() => {
        const strip = tabsRef.current;
        const selected = strip?.querySelector('[aria-selected="true"]');
        if (strip && selected && strip.scrollWidth > strip.clientWidth) {
            const stripBox = strip.getBoundingClientRect();
            const tabBox = selected.getBoundingClientRect();
            const left = strip.scrollLeft + (tabBox.left + tabBox.width / 2) - (stripBox.left + stripBox.width / 2);
            strip.scrollTo({ left, behavior: tabsPlaced.current && !prefersReducedMotion() ? 'smooth' : 'auto' });
        }
        tabsPlaced.current = true;
    }, [activeTab, tabs.length]);

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
            // Запоминается только первая страница без отбора — то, с чего «Заявки» открываются.
            if (!selectionKey && pageNumber === 1) {
                remember(userId, 'registry', { items: data.items || [], total: Number(data.total) || 0, counters: data.counters || {} });
            }
        } catch (error) {
            if (requestRef.current !== ticket) return;
            // Счётчики так и не пришли — плитки всё равно встают (с прочерками), иначе заготовка висела бы вечно.
            setCounters((prev) => prev || {});
            setLoadError(errorText(error, 'Не удалось загрузить реестр'));
        } finally {
            if (requestRef.current === ticket) setLoading(false);
        }
    }, [apiBaseUrl, headers, selectionKey, userId]);

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

    // Номер закрытой заявки из адреса уберёт эффект адреса выше.
    const closeCard = useCallback(() => setOpenedId(null), []);

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
                        <div className={`truncate font-medium ${text.main}`}>{request.expense_name}</div>
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
                        <div className={`font-semibold tabular-nums ${text.main}`}>{fmtMoney(netAmount(request))}</div>
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
                        {/* Бухгалтерия на «Согласовании» — это уточнение компании, а не согласование. */}
                        {request.current_kind === 'entity_clarify' && <div className={`truncate text-[12px] leading-4 ${text.meta}`}>уточнение компании</div>}
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
                    ? <div className={`whitespace-nowrap text-[12.5px] tabular-nums ${text.body}`}>{paidLabel(request)}</div>
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
    /* Кнопка «Создать заявку» видна сразу, по запомненным правам, а форме нужен полный ответ
       сервера: из него она берёт самого человека и его подразделение. Нажали раньше, чем он
       пришёл (первые доли секунды после перезагрузки), — форма откроется, как только придёт. */
    const openCreate = () => {
        if (!ping) { setCreateWanted(true); return; }
        setEditing(null);
        setFormOpen(true);
    };
    useEffect(() => {
        if (!createWanted || !ping) return;
        setCreateWanted(false);
        setEditing(null);
        setFormOpen(true);
    }, [createWanted, ping]);
    const narrowed = Boolean(query || filtersActive || state !== 'all');

    return (
        // На телефоне у корня свой непрозрачный фон: область раздела там прозрачна,
        // и фоновый логотип портала просвечивал бы серыми фигурами между карточками.
        // Корень — колонка раздела, одна на шапку, списки и доску: края у всех общие.
        <div className={`${SECTION_COLUMN} min-h-screen bg-gray-50 pb-12 pt-3 md:min-h-0`} style={{ fontFamily: APPLE_FONT }}>
            {/* Рост шапки — по кнопке, есть она или нет: иначе всё под шапкой сдвигалось на 7 px,
                когда кнопка появлялась. Пока о правах ничего не известно, на её месте заготовка —
                с теми же полями и тем же (невидимым) текстом, то есть точно того же размера. */}
            <header className="flex flex-wrap items-center justify-between gap-3 sm:min-h-[40px]">
                <h1 className="min-w-0 text-[22px] font-semibold tracking-tight text-slate-900">Оплата счетов</h1>
                {capabilities.can_create && (
                    <button type="button" className={`${iosBtnPrimary} w-full whitespace-nowrap sm:w-auto`} onClick={openCreate} disabled={!rolesReady}>
                        <Plus size={15} /> Создать заявку
                    </button>
                )}
                {!shellKnown && (
                    <span
                        aria-hidden="true"
                        data-create-skeleton
                        className="inline-flex w-full animate-pulse items-center justify-center gap-2 whitespace-nowrap rounded-xl bg-slate-200/70 px-4 py-2.5 text-[13.5px] font-semibold motion-reduce:animate-none sm:w-auto"
                    >
                        <span className="invisible inline-flex items-center gap-2"><Plus size={15} /> Создать заявку</span>
                    </span>
                )}
            </header>

            {/* Восемь вкладок администратора в полный размер занимают 957 px и на небольшом экране
                компьютера (1366 px при масштабе Windows 125 % — это 1093 px, 1280 px — 1024) уходили
                за край: последние вкладки не видно, а полоса прокрутки у ленты скрыта. От 769 до
                1179 px вкладки плотнее — поля 8 px и текст 12,5 px, — и лента помещается с 1010 px.
                Правило стоит на самих кнопках (`[&>button]`), а не утилитой рядом: размер кнопки
                задаёт IosSegmented, и одиночная утилита его не перебивает. */}
            {shellKnown ? (
                /* Лента стоит в краях раздела и на телефоне. Раньше у обёртки были `-mx-3 px-3` —
                   выпустить ленту до краёв экрана; но в телефонной оболочке отрицательные поля
                   гасятся (mobile-shell.css), а внутренние оставались, и лента стояла на 12 px
                   правее кнопки и карточек. */
                <div ref={tabsRef} className="mt-4 overflow-x-auto [scrollbar-width:none]">
                    <IosSegmented
                        // Вкладка из ссылки ещё ждёт прав — не выбрана ни одна, «Мои задачи» тоже.
                        value={tabPending ? tab : activeTab}
                        options={tabs}
                        onChange={changeTab}
                        size="lg"
                        ariaLabel="Раздел"
                        className="min-w-max min-[769px]:max-[1179px]:[&>button]:px-2 min-[769px]:max-[1179px]:[&>button]:text-[12.5px]"
                    />
                </div>
            ) : (
                /* Какие вкладки положены человеку, ещё не известно (первый вход на этом
                   устройстве). Двух вкладок «по умолчанию» не рисуем — через мгновение их стало
                   бы восемь; стоит пустая лента того же роста, вкладки встанут в неё. Рост даёт
                   невидимая вкладка с теми же классами, что у настоящих. */
                <div className="mt-4 flex rounded-[12px] bg-slate-100 p-[4px]" data-tabs-skeleton aria-hidden="true">
                    <span className="invisible px-3.5 py-2 text-[13.5px] font-semibold min-[769px]:max-[1179px]:text-[12.5px]">Мои задачи</span>
                </div>
            )}

            {pingError && <div className="mt-4"><NoticeBox text={pingError} /></div>}
            {known && known.schema_ready === false && (
                <div className="mt-4"><NoticeBox text="Раздел разворачивается — таблицы появятся после перезапуска сервера." /></div>
            )}
            {known && !rolesReady && (
                <div className="mt-4">
                    <NoticeBox text={`Заявки пока не завести: не назначены участники — ${rolesMissing.join(', ')}.`}>
                        {isAdmin && (
                            <>
                                {' '}
                                <button type="button" className="font-medium underline decoration-amber-300 underline-offset-2 hover:decoration-amber-600" onClick={() => changeTab('roles')}>Открыть «Участники»</button>
                            </>
                        )}
                    </NoticeBox>
                </div>
            )}
            {known && known.storage_ready === false && (
                <div className="mt-3"><NoticeBox tone="slate" text="Хранилище файлов не настроено: счета, чеки и акты прикрепить не получится, пока не задан бакет." /></div>
            )}
            {known && isAdmin && known.legacy_pending > 0 && (
                <div className="mt-3"><NoticeBox text={`Заявок прежней версии процесса не переведено на этапы: ${known.legacy_pending}. Работать с ними нельзя, пока перенос не пройдёт, — причина записана в журнале сервера.`} /></div>
            )}
            {known && isAdmin && known.card_key_ready === false && (
                <div className="mt-3"><NoticeBox tone="slate" text="Не задан ключ шифрования номеров карт: заявки на пополнение карты сохранить не получится." /></div>
            )}

            {tabPending && <LinkPending kind="tab" className="mt-4" />}

            {/* Вкладка не подменяется рывком: содержимое мягко проявляется (fade-in-soft). */}
            {activeTab === 'desk' && !tabPending && (
                <div className="mt-4 motion-safe:animate-fade-in-soft">
                    <PaymentsDesk
                        apiBaseUrl={apiBaseUrl}
                        headers={headers}
                        refreshKey={refreshKey}
                        soonDays={soonDays}
                        onOpen={setOpenedId}
                        onCount={setDeskCount}
                        memoryKey={userId}
                        expected={deskCount}
                    />
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

                    {/* Срезы реестра — плитками со счётчиками (она же легенда цвета строк). Состояние
                        без заявок не показывается, пока его не выбрали: пустая плитка — шум. */}
                    <div className="mt-3">
                        {counters ? (
                            <StatTiles
                                ariaLabel="Состояние заявок"
                                value={state}
                                onChange={setState}
                                items={STATE_FILTERS
                                    .filter((item) => ['all', 'mine', 'open'].includes(item.key) || counters[item.counter] || state === item.key)
                                    .map((item) => ({ key: item.key, label: item.label, tone: item.tone, count: counters[item.counter] }))}
                            />
                        ) : (
                            /* Какие состояния есть у заявок, станет известно с ответом сервера. До него — заготовка
                               того же роста: плитки, выраставшие с трёх до восьми, были бы лишним движением. */
                            <StatTilesSkeleton label="Загружаем состояния заявок" />
                        )}
                    </div>

                    {loadError && <div className="mt-4 rounded-2xl bg-red-50 px-4 py-3 text-[13px] text-red-700 ring-1 ring-red-200">{loadError}</div>}

                    {/* На широком экране — таблица, у которой карточкой нарисована каждая строка (как в
                        списках «Задач»): записи отделены зазором, подписи колонок стоят над списком.
                        У левого края строки — полоса типа заявки, того же цвета, что на карточках досок.
                        На телефоне — карточки. */}
                    <div ref={listRef} className="scroll-mt-4" />
                    <div className="mt-2 hidden overflow-x-auto md:block">
                        {items.length > 0 && (
                            <table className={CARD_TABLE} style={{ minWidth: tableMinWidth }} data-registry>
                                <colgroup>
                                    {visibleColumns.map((column) => <col key={column.key} style={column.width ? { width: column.width } : undefined} />)}
                                </colgroup>
                                <thead>
                                    <tr>
                                        {visibleColumns.map((column, index) => (
                                            <th key={column.key} className={`${CARD_TH} ${column.align === 'right' ? '!text-right' : ''} ${index === 0 ? '!pl-5' : ''}`}>{column.label}</th>
                                        ))}
                                    </tr>
                                </thead>
                                <tbody>
                                    {items.map((request, index) => {
                                        const tone = rowTone(request);
                                        const type = requestType(request);
                                        const closedStart = closedGroupStart(items, index);
                                        return (
                                            <React.Fragment key={request.id}>
                                                {closedStart && (
                                                    <tr data-registry-group="closed">
                                                        <td colSpan={visibleColumns.length} className="p-0"><GroupDivider>Завершённые</GroupDivider></td>
                                                    </tr>
                                                )}
                                                <tr data-request={request.id} onClick={() => setOpenedId(request.id)} className={`${CARD_ROW} h-[60px]`}>
                                                    {visibleColumns.map((column, cell) => (
                                                        <td
                                                            key={column.key}
                                                            title={cell === 0 ? `Тип заявки: ${type.label}` : undefined}
                                                            className={`${cardCell(tone)} align-middle ${column.align === 'right' ? 'text-right' : ''} ${cell === 0
                                                                ? `relative !pl-5 before:pointer-events-none before:absolute before:bottom-2.5 before:left-2 before:top-2.5 before:w-[3px] before:rounded-full ${type.bar}`
                                                                : ''}`}
                                                        >
                                                            {renderCell(column.key, request, toneText(tone))}
                                                        </td>
                                                    ))}
                                                </tr>
                                            </React.Fragment>
                                        );
                                    })}
                                </tbody>
                            </table>
                        )}
                        {!items.length && !loading && (
                            <div className={`${iosCard} mt-1.5 flex flex-col items-center gap-2 px-4 py-12 text-center`}>
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
                    <div className="mt-3 space-y-2 md:hidden">
                        {items.map((request, index) => {
                            const tone = rowTone(request);
                            const where = registryWhere(request);
                            return (
                                <React.Fragment key={request.id}>
                                {/* Та же подпись на стыке, что в таблице: дальше — завершённые заявки. */}
                                {closedGroupStart(items, index) && <GroupDivider>Завершённые</GroupDivider>}
                                <button
                                    type="button"
                                    data-request-card={request.id}
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
                                </React.Fragment>
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
                        openedAsset={assetId}
                        onOpenAsset={setAssetId}
                    />
                </div>
            )}

            {activeTab === 'dictionaries' && (
                <div className="mt-4 motion-safe:animate-fade-in-soft">
                    {dictionaryPending ? <LinkPending kind="dictionary" /> : (
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
                        section={activeDictionary}
                        onSectionChange={changeDictionary}
                    />
                    )}
                </div>
            )}

            {activeTab === 'roles' && (
                <div className="mt-4 motion-safe:animate-fade-in-soft">
                    {ping ? (
                        <PaymentsRoles
                            apiBaseUrl={apiBaseUrl}
                            headers={headers}
                            roles={ping.roles || {}}
                            users={users}
                            onChanged={(roles) => { setPing((prev) => (prev ? { ...prev, roles } : prev)); loadPing(); }}
                            showToast={showToast}
                            canEdit={isAdmin}
                        />
                    ) : (
                        /* Кто в какой роли, приходит только с полным ответом сервера (в запомненной
                           оболочке раздела имён нет). Без него роли выглядели бы пустыми — «Пока
                           никого. Без участника этой роли заявки не заводятся». */
                        <div className="grid gap-4 lg:grid-cols-2" role="status" aria-busy="true" aria-label="Загружаем участников">
                            {[0, 1, 2, 3].map((index) => <Bone key={index} className="h-[132px] !rounded-2xl" />)}
                        </div>
                    )}
                </div>
            )}

            <PaymentRequestForm
                open={formOpen}
                // Доработку открывают из карточки заявки — туда же и возвращаемся, а не в список.
                onClose={() => {
                    const back = editing?.request?.id;
                    setFormOpen(false);
                    setEditing(null);
                    setRevealIssues(false);
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
                revealIssues={revealIssues}
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
                // Из задачи инициатора «Доработать заявку» — форма сразу показывает, чего не хватает.
                onEdit={(data, options) => { setOpenedId(null); setEditing(data); setRevealIssues(Boolean(options?.reveal)); setFormOpen(true); }}
                showToast={showToast}
            />
        </div>
    );
};

export default PaymentsView;
