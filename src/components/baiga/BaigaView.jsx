import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import {
    ArrowRight, Bike, CalendarDays, ChevronRight, ClipboardList, Download, Layers, Loader2, MapPin, Search, SearchX,
    SlidersHorizontal, Upload, X,
} from 'lucide-react';
import { APPLE_FONT, IosMenu, IosPager, IosSegmented, iosBtnPrimary, iosCard, iosGroupLabel } from '../ui/ios';
import CustomSelect from '../ui/CustomSelect';
import useIsMobileShell from '../common/useIsMobileShell';
import BaigaDriverSheet from './BaigaDriverSheet';
import BaigaJournal from './BaigaJournal';
import BaigaListModal from './BaigaListModal';
import BaigaUploadModal from './BaigaUploadModal';
import {
    CopyButton, HeaderPill, PrizePill, SectionHeader, TOOLBAR_BTN, TOOLBAR_BTN_ON,
} from './baigaUi';
import {
    ALL_SHEETS, COLUMN_LABELS, EMPTY_FILTERS, PAGE_SIZES, PRIZE_ANY, PRIZE_NONE, RANGE_FIELDS, SEARCH_DEBOUNCE_MS,
    fileNameFromDisposition, filterChips, filtersPayload, focusFilters, formatInt, formatMoney, groupRows, hasAnyFilter,
    isAllSheets, isGrouped, isSearching, nextSort, panelFilterCount, periodLabel, plural, rangeErrors,
    readStateFromSearch, shortId, splitTokens, weekLabel, weekShort, writeStateToAddressBar,
} from './baigaMeta';

/*
 * Раздел «Списки Байги» (#356) — итоги еженедельной акции Байга.
 *
 * Аналитик раз в неделю загружает Excel, все недели лежат в одной таблице, а
 * сотрудники ищут по ней водителей и выгружают выборку обратно в Excel.
 *
 * ЛИСТЫ — КАК В EXCEL (просьба владельца 02.10.2026: «пусть будет такое же
 * разделение по листам как у эксельки»). Над таблицей — вкладки листов файла
 * (зачёты: группы городов и «МОТО БАЙГА»), открыт один лист — по умолчанию
 * первый, как Excel открывает файл. Вкладка «Все листы» показывает книгу
 * целиком, и строки там разделены заголовками листов. Поиск по ФИО, ВУ или ID
 * ищет по всей книге, как «Найти всё»: на время поиска раздел сам переходит
 * на «Все листы», а когда поиск стёрт — возвращается на прежний лист.
 *
 * Раскладка — как у соседних разделов («Термокороба», «Посылки»): шапка с
 * плиткой-значком, строка инструментов высотой 36 px (поиск, неделя,
 * «Фильтры», «Список ID/ВУ»), остальные фильтры — под кнопкой, выбранное видно
 * чипами. Нажатие на строку открывает карточку водителя со всеми его неделями.
 *
 * Шум убран там, где он повторяется: колонка «Неделя» видна, только когда в
 * выборке больше одной недели; колонка «Зачёт» — только там, где листы
 * перемешаны (сортировка по сумме, ФИО…). Цвет — только у приза.
 */

const errorOf = (requestError, fallback) => requestError?.response?.data?.error || fallback;

/* Ошибка запроса с responseType: 'blob' приходит Blob'ом — текст достаём сами. */
const blobErrorOf = async (requestError, fallback) => {
    const data = requestError?.response?.data;
    if (data && typeof data.text === 'function') {
        try {
            return JSON.parse(await data.text())?.error || fallback;
        } catch (error) {
            return fallback;
        }
    }
    return errorOf(requestError, fallback);
};

const saveBlob = (blob, name) => {
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = name;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
};

/* Стрелка сортировки — своим SVG: символы ↑↓ системные шрифты рисуют цветным
   эмодзи, и в шапке таблицы это первое, что бросается в глаза. */
const SortArrow = ({ active, dir }) => (
    <svg width="7" height="10" viewBox="0 0 7 10" aria-hidden="true"
         className={`shrink-0 transition ${active ? 'text-slate-700' : 'text-slate-400 opacity-0 group-hover:opacity-60'}`}>
        <path d="M3.5 0 L7 4 H0 Z" fill="currentColor" opacity={active && dir === 'desc' ? 0.25 : 1} />
        <path d="M3.5 10 L0 6 H7 Z" fill="currentColor" opacity={active && dir === 'asc' ? 0.25 : 1} />
    </svg>
);

const SortHeader = ({ column, sort, onSort, align = 'left', className = '' }) => {
    const active = sort.sort === column;
    return (
        <th className={`whitespace-nowrap px-2 py-2.5 font-semibold 2xl:px-3 ${align === 'right' ? 'text-right' : ''} ${className}`}
            aria-sort={active ? (sort.dir === 'asc' ? 'ascending' : 'descending') : 'none'}>
            <button type="button" onClick={() => onSort(column)}
                    className={`group inline-flex items-center gap-1.5 uppercase tracking-wider transition hover:text-slate-800 ${
                        align === 'right' ? 'flex-row-reverse' : ''} ${active ? 'text-slate-800' : ''}`}>
                {COLUMN_LABELS[column]}
                <SortArrow active={active} dir={sort.dir} />
            </button>
        </th>
    );
};

/* Значок листа: у мото-зачёта — мотоцикл, у групп городов — метка места. */
const sheetIcon = (name) => (/мото/i.test(String(name || '')) ? Bike : MapPin);

/* Листы — как листы книги Excel, но крупно, чтобы лист было видно с первого
   взгляда (просьба владельца: «выделение городов не такое заметное, сделай
   более заметным и красивее»). Каждый лист — карточка: значок, название и
   число водителей в текущей выборке (поиск и фильтры учтены) — по нему видно,
   где искомый водитель. Открытый лист закрашен синим, как выбранный элемент
   в macOS; лист без строк в выборке — бледный.

   На компьютере — ровная сетка: от 1024 px пять колонок, то есть два ряда по
   пять одинаковых плиток. На телефоне —
   ряд, который листается вбок. Перенос и прокрутка заданы стилем, а не
   классом: оболочка телефона (mobile-shell.css) переносит любой ряд с gap-*
   по строкам, а сетки grid-cols-* сводит к двум колонкам. */
/* Плитки ОДНОГО размера (просьба владельца: «сделай более симметричными, у
   каждого разный размер»). Высота задана, и раскладка внутри одна у всех:
   значок слева, название посередине, число водителей — значком справа. Перенос
   длинного названия раньше делал свой ряд выше соседнего; теперь название
   занимает до двух строк ВНУТРИ той же высоты и стоит по центру, поэтому
   плитки с короткими и длинными названиями одинаковы. Что не влезло и в две
   строки — многоточием; целиком название во всплывающей подсказке и крупно
   в заголовке листа под сеткой. */
const TWO_LINES = { display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' };

const SheetCard = ({ label, count, active, Icon, onClick, narrow }) => (
    <button
        type="button"
        role="tab"
        aria-selected={active}
        onClick={onClick}
        title={label}
        style={narrow ? { width: 210, flex: '0 0 auto' } : undefined}
        className={`flex h-[60px] min-w-0 items-center gap-2.5 rounded-2xl pl-2.5 pr-3 text-left transition active:scale-[0.98] ${
            active
                ? 'bg-blue-600 shadow-[0_4px_12px_rgba(37,99,235,0.25)]'
                : count
                    ? 'bg-white ring-1 ring-slate-200/80 shadow-[0_1px_2px_rgba(15,23,42,0.05)] hover:ring-slate-300 hover:shadow-[0_4px_12px_rgba(15,23,42,0.08)]'
                    : 'bg-white ring-1 ring-slate-200/70'
        }`}
    >
        <span className={`grid h-8 w-8 shrink-0 place-items-center rounded-[10px] ${
            active ? 'bg-white/20 text-white' : count ? 'bg-blue-50 text-blue-600' : 'bg-slate-100 text-slate-300'
        }`}>
            <Icon size={16} />
        </span>
        <span className={`min-w-0 grow basis-0 text-[12.5px] font-semibold leading-[1.25] ${
            active ? 'text-white' : count ? 'text-slate-900' : 'text-slate-400'
        }`} style={TWO_LINES}>
            {label}
        </span>
        <span className={`shrink-0 rounded-md px-1.5 py-0.5 text-[11.5px] font-semibold tabular-nums ${
            active ? 'bg-white/20 text-white' : count ? 'bg-slate-100 text-slate-600' : 'bg-slate-50 text-slate-300'
        }`}>
            {formatInt(count)}
        </span>
    </button>
);

const SheetTabs = ({ sheets, counts, total, value, onChange }) => {
    const narrow = useIsMobileShell();
    const cards = [
        <SheetCard key={ALL_SHEETS} label="Все листы" count={total} Icon={Layers}
                   active={isAllSheets(value)} onClick={() => onChange(ALL_SHEETS)} narrow={narrow} />,
        ...sheets.map((name) => {
            const count = counts.get(name)?.rows || 0;
            return (
                <SheetCard key={name} label={name} count={count} Icon={sheetIcon(name)}
                           active={value === name} onClick={() => onChange(name)} narrow={narrow} />
            );
        }),
    ];
    if (narrow) {
        return (
            <div role="tablist" aria-label="Листы" className="thin-scroll mt-4 flex gap-2 pb-1"
                 style={{ flexWrap: 'nowrap', overflowX: 'auto' }}>
                {cards}
            </div>
        );
    }
    return (
        <div role="tablist" aria-label="Листы" className="mt-4 grid grid-cols-2 gap-2 lg:grid-cols-5">
            {cards}
        </div>
    );
};

const GroupIcon = ({ name }) => {
    const Icon = sheetIcon(name);
    return <Icon size={14} />;
};

/* Ячейка таблицы: на ноутбуке (до 1536 px) поля чуть уже — одиннадцать колонок иначе не
   помещались и переносили ФИО на две строки. */
const CELL = 'px-2 py-2.5 2xl:px-3';

const RangeInput = ({ value, onChange, placeholder, invalid, label }) => (
    <input
        className={`h-9 w-full min-w-0 rounded-xl border-0 px-3 text-[13.5px] tabular-nums transition focus:outline-none focus:ring-2 focus:ring-blue-500/70 ${
            invalid ? 'bg-rose-50 text-rose-700 ring-1 ring-rose-300' : 'bg-slate-100 text-slate-900 focus:bg-white'
        }`}
        inputMode="numeric"
        value={value}
        placeholder={placeholder}
        aria-label={label}
        onChange={(event) => onChange(event.target.value)}
    />
);

/* Что набирают по букве и по цифре — поиск и диапазоны. Только их правка
   ждёт паузы перед запросом; остальное (лист, неделя, город, список,
   страница) — нажатие, и ждать после него нечего. */
const typedKeyOf = (payload) => JSON.stringify([
    payload.q || '',
    ...RANGE_FIELDS.flatMap(({ key }) => [payload[`${key}_min`] ?? '', payload[`${key}_max`] ?? '']),
]);

const BaigaView = ({ apiBaseUrl, withAccessTokenHeader, showToast, focus = null, onFocusConsumed }) => {
    const headers = useCallback(
        () => (withAccessTokenHeader ? withAccessTokenHeader() : {}),
        [withAccessTokenHeader],
    );
    // showToast из App.jsx — новая функция на каждый рендер: держим её в ref,
    // иначе загрузка перезапускалась бы после каждого тоста.
    const toastRef = useRef(showToast);
    useEffect(() => { toastRef.current = showToast; }, [showToast]);
    const toast = useCallback((...args) => toastRef.current?.(...args), []);

    const initial = useMemo(
        () => readStateFromSearch(typeof window !== 'undefined' ? window.location.search : ''), [],
    );

    const [screen, setScreen] = useState(null);
    const [screenError, setScreenError] = useState('');
    const [tab, setTab] = useState('search');
    const [filters, setFilters] = useState(initial.filters);
    const [sort, setSort] = useState(initial.sort);
    const [size, setSize] = useState(initial.size);
    const [page, setPage] = useState(1);
    const [result, setResult] = useState(null);
    const [loading, setLoading] = useState(false);
    const [searchError, setSearchError] = useState('');
    const [filtersOpen, setFiltersOpen] = useState(() => panelFilterCount(initial.filters) > 0);
    const [listOpen, setListOpen] = useState(false);
    const [uploadOpen, setUploadOpen] = useState(false);
    const [exporting, setExporting] = useState(false);
    const [reloadKey, setReloadKey] = useState(0);
    const [driver, setDriver] = useState(null);

    // Текущие фильтры — и в ref: правки собираются из актуального значения без
    // побочных действий внутри setState (запоминание листа на время поиска).
    const filtersRef = useRef(filters);
    filtersRef.current = filters;
    // Лист, с которого ушли на «Все листы», когда начался поиск, — вернуться
    // на него, когда поиск стёрт.
    const savedSheet = useRef(null);
    // Первый лист открывается сам один раз — когда известен список листов.
    const sheetDefaulted = useRef(Boolean(initial.filters.zachet));

    const loadScreen = useCallback(() => {
        setScreenError('');
        return axios.get(`${apiBaseUrl}/api/baiga`, { headers: headers() })
            .then((response) => {
                const data = response.data || null;
                setScreen(data);
                // Как Excel при открытии файла — первый лист. Тем же тиком, что и
                // экран: иначе ушёл бы лишний запрос «все листы» до выбора листа.
                const first = data?.options?.zachets?.[0];
                if (!sheetDefaulted.current && first) {
                    sheetDefaulted.current = true;
                    const current = filtersRef.current;
                    if (!current.zachet && !isSearching(current)) {
                        const next = { ...current, zachet: first };
                        filtersRef.current = next;
                        setFilters(next);
                    }
                }
            })
            .catch((requestError) => setScreenError(errorOf(requestError, 'Не удалось открыть раздел')));
    }, [apiBaseUrl, headers]);

    useEffect(() => { loadScreen(); }, [loadScreen]);

    const capabilities = screen?.capabilities || {};
    const limits = screen?.limits || {};
    const options = screen?.options || {};
    const sheets = useMemo(() => options.zachets || [], [options.zachets]);
    const weeks = useMemo(() => screen?.weeks || [], [screen]);
    const weekByStart = useMemo(() => new Map(weeks.map((week) => [week.period_start, week])), [weeks]);
    const latest = weeks[0] || null;

    /* ── Правка фильтров ───────────────────────────────────────────────── */

    const updateFilters = useCallback((patch, { pickSheet = false } = {}) => {
        const prev = filtersRef.current;
        const next = { ...prev, ...patch };
        if (pickSheet) {
            // Лист выбран человеком — его выбор важнее возврата после поиска.
            savedSheet.current = null;
        } else if (!isSearching(prev) && isSearching(next)) {
            savedSheet.current = prev.zachet || null;
            next.zachet = ALL_SHEETS;
        } else if (isSearching(prev) && !isSearching(next) && savedSheet.current) {
            next.zachet = savedSheet.current;
            savedSheet.current = null;
        }
        filtersRef.current = next;
        setFilters(next);
        setPage(1);
    }, []);

    const pickSheet = useCallback((zachet) => updateFilters({ zachet }, { pickSheet: true }), [updateFilters]);

    const changeSort = useCallback((column) => {
        setSort((prev) => nextSort(prev, column));
        setPage(1);
    }, []);

    // «Сбросить всё» снимает фильтры, но не уводит с открытого листа: лист —
    // это навигация, как вкладка книги, а не фильтр.
    const resetFilters = () => updateFilters({ ...EMPTY_FILTERS, zachet: filtersRef.current.zachet });

    const reloadAll = useCallback(() => {
        loadScreen();
        setPage(1);
        setReloadKey((value) => value + 1);
    }, [loadScreen]);

    // Лист, которого больше нет (неделю удалили или заменили другим файлом), —
    // открыть первый существующий, а не показывать пустоту.
    useEffect(() => {
        const current = filtersRef.current.zachet;
        if (sheets.length && current && current !== ALL_SHEETS && !sheets.includes(current)) {
            pickSheet(sheets[0]);
        }
    }, [sheets, pickSheet]);

    /* Просьба помощника — источник «Списки Байги» под его ответом: открыть
       неделю из ответа и найти водителя. Ждёт экран (без него неизвестны
       загруженные недели) и исполняется один раз. Номер ВУ ложится в строку
       поиска, а не в адрес: в адрес поиск не пишется вовсе (baigaMeta.js). */
    useEffect(() => {
        if (!focus || !screen) return;
        updateFilters(focusFilters(focus, weeks, filtersRef.current.zachet));
        setTab('search');
        setDriver(null);
        onFocusConsumed?.();
    }, [focus, screen, weeks, updateFilters, onFocusConsumed]);

    /* ── Поиск ─────────────────────────────────────────────────────────── */

    const payload = useMemo(() => filtersPayload(filters), [filters]);
    const payloadKey = JSON.stringify(payload);
    const typedKey = typedKeyOf(payload);
    const requestKey = JSON.stringify({ payloadKey, sort, page, size, reloadKey });
    const requestSeq = useRef(0);
    const hasResult = useRef(false);
    const lastTypedKey = useRef(typedKey);
    // Пока первый лист не выбран, запрос «все листы» был бы лишним.
    const waitingForSheet = !sheetDefaulted.current && !filters.zachet && !isSearching(filters);

    useEffect(() => {
        if (!screen?.schema_ready || waitingForSheet) return undefined;
        const id = requestSeq.current + 1;
        requestSeq.current = id;
        const typing = hasResult.current && lastTypedKey.current !== typedKey;
        lastTypedKey.current = typedKey;
        const requestedKey = payloadKey;
        const timer = setTimeout(() => {
            setLoading(true);
            axios.post(`${apiBaseUrl}/api/baiga/rows`,
                { filters: payload, sort: sort.sort, dir: sort.dir, page, size }, { headers: headers() })
                .then((response) => {
                    if (id !== requestSeq.current) return;
                    hasResult.current = true;
                    // Ключ запроса — вместе с ответом: плашка списка и итоги
                    // относятся только к ТОЙ выборке, по которой их посчитали.
                    setResult({ ...(response.data || {}), key: requestedKey });
                    setSearchError('');
                })
                .catch((requestError) => {
                    if (id === requestSeq.current) setSearchError(errorOf(requestError, 'Не удалось выполнить поиск'));
                })
                .finally(() => { if (id === requestSeq.current) setLoading(false); });
        }, typing ? SEARCH_DEBOUNCE_MS : 0);
        return () => clearTimeout(timer);
        // payload/sort/page/size входят в requestKey.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [requestKey, screen?.schema_ready, waitingForSheet, apiBaseUrl, headers]);

    useEffect(() => { writeStateToAddressBar({ filters, sort, size }); }, [filters, sort, size]);

    const totals = result?.totals || null;
    const rows = result?.rows || [];
    const total = totals?.rows || 0;
    const pageCount = Math.max(1, Math.ceil(total / size));

    // Строк стало меньше (неделю удалили, заменили коротким файлом), а открыта
    // дальняя страница — назад на последнюю, иначе пусто и без пейджера.
    useEffect(() => {
        if (result && page > pageCount) setPage(pageCount);
    }, [result, page, pageCount]);

    const errors = rangeErrors(filters);
    const chips = filterChips(filters);
    const panelCount = panelFilterCount(filters);
    const listCount = splitTokens(filters.list).length;
    const filtered = hasAnyFilter({ ...filters, zachet: '' });
    const allSheets = isAllSheets(filters.zachet);
    // Одна неделя в выборке — колонка «Неделя» была бы одним и тем же значением
    // в каждой строке; её нет, а неделя видна в выборе недели.
    const showWeek = weeks.length > 1 && !filters.period;
    // «Все листы» в порядке файла: строки разделены заголовками листов, колонка
    // «Зачёт» не нужна. При сортировке по колонке листы перемешаны — там она есть.
    const grouped = isGrouped(filters, sort);
    const showZachetColumn = allSheets && !grouped;
    const sheetCounts = useMemo(() => new Map((result?.zachets || []).map((item) => [item.name, item])),
        [result]);
    const sheetsTotal = (result?.zachets || []).reduce((sum, item) => sum + item.rows, 0);
    const tableRows = grouped ? groupRows(rows, showWeek) : rows.map((row) => ({ type: 'row', row }));
    const columnCount = 9 + (showWeek ? 1 : 0) + (showZachetColumn ? 1 : 0) + 1;
    const freshResult = result && result.key === payloadKey;

    /* ── Выгрузка ──────────────────────────────────────────────────────── */

    const exportXlsx = async (mode, scopePayload) => {
        if (exporting) return;
        setExporting(true);
        try {
            const response = await axios.post(`${apiBaseUrl}/api/baiga/export`,
                { filters: scopePayload, mode, sort: sort.sort, dir: sort.dir },
                { headers: headers(), responseType: 'blob' });
            saveBlob(response.data, fileNameFromDisposition(response.headers?.['content-disposition'], 'Список байги.xlsx'));
            const count = Number(response.headers?.['x-rows'] || 0);
            toast(count ? `Выгружено ${formatInt(count)} ${plural(count, 'строка', 'строки', 'строк')}` : 'Файл готов',
                'success');
        } catch (requestError) {
            toast(await blobErrorOf(requestError, 'Не удалось выгрузить'), 'error');
        } finally {
            setExporting(false);
        }
    };

    /* ── Экран ─────────────────────────────────────────────────────────── */

    /* Непрозрачный фон корня — на телефоне: там `.main-content` прозрачен, и в
       зазорах между карточками просвечивал бы фоновый логотип (тот же приём,
       что у «Термокоробов»). */
    const shell = (children) => (
        <div className="relative mx-auto min-h-screen w-full max-w-[1600px] bg-gray-50 px-3 py-4 sm:px-5 sm:py-6 md:min-h-0"
             style={{ fontFamily: APPLE_FONT }}>
            {children}
        </div>
    );

    if (screenError) {
        return shell(<div className={`${iosCard} p-6 text-center text-[13.5px] text-slate-600`}>{screenError}</div>);
    }
    if (!screen) {
        return shell(
            <div className="flex items-center justify-center gap-2 py-16 text-[13px] text-slate-500">
                <Loader2 size={15} className="animate-spin" /> Открываем списки Байги…
            </div>,
        );
    }
    if (screen.schema_ready === false) {
        return shell(
            <div className="rounded-2xl bg-amber-50 px-4 py-3 text-[13px] text-amber-800 ring-1 ring-amber-200">
                Раздел разворачивается — он появится после перезапуска сервера.
            </div>,
        );
    }

    // Выгрузка называет, ЧТО уйдёт в файл: открыт лист — сам лист или вся книга.
    // Иначе «как исходник» с открытого листа отдавала бы один лист, а ждут книгу.
    // IosMenu ждёт в icon пункта сам компонент иконки, а не элемент: <Download />
    // здесь роняет страницу целиком (React #130) в момент открытия меню.
    const bookPayload = { ...payload };
    delete bookPayload.zachet;
    const exportMenuItems = allSheets ? [
        { key: 'book', label: 'Все листы — как исходник', icon: Download, onSelect: () => exportXlsx('sheets', bookPayload) },
        { key: 'single', label: 'Одним листом', icon: Download, onSelect: () => exportXlsx('single', bookPayload) },
    ] : [
        { key: 'sheet', label: `Лист «${filters.zachet}»`, icon: Download, onSelect: () => exportXlsx('sheets', payload) },
        { key: 'book', label: 'Все листы — как исходник', icon: Download, onSelect: () => exportXlsx('sheets', bookPayload) },
        { key: 'single', label: 'Все листы одним листом', icon: Download, onSelect: () => exportXlsx('single', bookPayload) },
    ];

    const weekOptions = [
        { value: '', label: 'Все недели' },
        ...weeks.map((week) => ({ value: week.period_start, label: weekLabel(week) })),
    ];
    const prizeOptions = [
        { value: '', label: 'Любой' },
        { value: PRIZE_ANY, label: 'Только с призом' },
        { value: PRIZE_NONE, label: 'Без приза' },
        ...(options.prizes || []).map((prize) => ({ value: prize.name, label: prize.name, groupLabel: 'Приз' })),
    ];
    const listSelect = (values, emptyLabel) => [
        { value: '', label: emptyLabel },
        ...(values || []).map((value) => ({ value, label: value })),
    ];

    // На телефоне у кнопок шапки остаются значки: три подписи в ряд на 390 px
    // разрывали шапку на две строки.
    const header = (
        <SectionHeader
            // Про выгрузку — только тому, у кого она есть: читающему подпись
            // обещала бы кнопку, которой у него нет.
            subtitle={capabilities.can_export
                ? 'Итоги еженедельной акции: поиск водителей и выгрузка в Excel'
                : 'Итоги еженедельной акции: поиск водителей'}
            // Пилюля — только на широком экране (от 1536 px): на ноутбуке она выталкивала
            // кнопки шапки во второй ряд, а неделя и так видна в выборе недели.
            badge={latest && (
                <span className="hidden 2xl:inline-flex">
                    <HeaderPill icon={CalendarDays}>
                        {latest.week_number ? `Неделя ${latest.week_number} · ` : ''}
                        {periodLabel(latest.period_start, latest.period_end)}
                    </HeaderPill>
                </span>
            )}
            actions={(
                <>
                    {capabilities.can_manage && (
                        <IosSegmented
                            value={tab}
                            onChange={setTab}
                            options={[{ value: 'search', label: 'Поиск' }, { value: 'journal', label: 'Журнал' }]}
                            ariaLabel="Раздел"
                        />
                    )}
                    {capabilities.can_export && tab === 'search' && (
                        <IosMenu
                            items={exportMenuItems}
                            label="Выгрузить в Excel"
                            disabled={exporting || !(sheetsTotal || total)}
                            trigger={{
                                icon: exporting ? <Loader2 size={15} className="animate-spin" /> : <Download size={15} />,
                                text: <span className="hidden sm:inline">{exporting ? 'Готовим файл…' : 'Выгрузить'}</span>,
                            }}
                        />
                    )}
                    {capabilities.can_manage && (
                        <button type="button" className={iosBtnPrimary} onClick={() => setUploadOpen(true)}
                                aria-label="Загрузить неделю">
                            <Upload size={15} />
                            <span className="hidden sm:inline">Загрузить неделю</span>
                        </button>
                    )}
                </>
            )}
        />
    );

    const modals = (
        <>
            <BaigaListModal
                open={listOpen}
                value={filters.list}
                maxTokens={limits.max_list || 2000}
                onApply={(text) => updateFilters({ list: text })}
                onClose={() => setListOpen(false)}
            />
            <BaigaDriverSheet
                row={driver}
                onClose={() => setDriver(null)}
                apiBaseUrl={apiBaseUrl}
                headers={headers}
                weekByStart={weekByStart}
            />
            {capabilities.can_manage && (
                <BaigaUploadModal
                    open={uploadOpen}
                    onClose={() => setUploadOpen(false)}
                    apiBaseUrl={apiBaseUrl}
                    headers={headers}
                    maxUploadMb={limits.max_upload_mb || 20}
                    onUploaded={reloadAll}
                    toast={toast}
                />
            )}
        </>
    );

    if (tab === 'journal' && capabilities.can_manage) {
        return shell(
            <>
                {header}
                <BaigaJournal apiBaseUrl={apiBaseUrl} headers={headers} toast={toast} onChanged={reloadAll}
                              refreshKey={reloadKey} />
                {modals}
            </>,
        );
    }

    if (!weeks.length) {
        return shell(
            <>
                {header}
                <div className={`${iosCard} flex flex-col items-center px-6 py-14 text-center`}>
                    <span className="grid h-12 w-12 place-items-center rounded-2xl bg-slate-100 text-slate-400">
                        <CalendarDays size={22} />
                    </span>
                    <div className="mt-3 text-[15px] font-semibold text-slate-800">Недель пока нет</div>
                    <p className="mt-1 max-w-sm text-[13px] text-slate-500">
                        {capabilities.can_manage
                            ? 'Загрузите файл недели — кнопка «Загрузить неделю» вверху.'
                            : 'Списки появятся, когда загрузят первую неделю.'}
                    </p>
                </div>
                {modals}
            </>,
        );
    }

    const listResult = freshResult ? result.list : null;

    return shell(
        <>
            {header}

            {/* Строка инструментов: всё одной высоты (36 px), как у CustomSelect. */}
            <div className="flex flex-col gap-2 lg:flex-row lg:items-center">
                <div className="relative min-w-0 lg:flex-1">
                    <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                    <input
                        type="text"
                        inputMode="search"
                        className="h-9 w-full rounded-xl border-0 bg-white pl-9 pr-8 text-[13.5px] text-slate-900 shadow-[0_1px_2px_rgba(15,23,42,0.04)] ring-1 ring-slate-200/70 placeholder-slate-400 transition focus:outline-none focus:ring-2 focus:ring-blue-500/60"
                        placeholder="ФИО, номер ВУ или ID — по всем листам"
                        value={filters.q}
                        onChange={(event) => updateFilters({ q: event.target.value })}
                        aria-label="Поиск по ФИО, номеру ВУ или ID водителя"
                    />
                    {filters.q && (
                        <button type="button" onClick={() => updateFilters({ q: '' })} aria-label="Очистить поиск"
                                className="absolute right-2 top-1/2 grid h-5 w-5 -translate-y-1/2 place-items-center rounded-full bg-slate-300 text-white transition hover:bg-slate-400">
                            <X size={11} strokeWidth={3} />
                        </button>
                    )}
                </div>
                <div className="flex items-center gap-2">
                    <CustomSelect
                        value={filters.period}
                        onChange={(value) => updateFilters({ period: value || '' })}
                        options={weekOptions}
                        variant="ios"
                        textClassName="text-[13px] font-medium text-slate-800"
                        className="min-w-0 grow basis-0 lg:w-[280px] lg:grow-0 lg:basis-auto"
                        ariaLabel="Неделя"
                    />
                    <button
                        type="button"
                        className={panelCount ? TOOLBAR_BTN_ON : TOOLBAR_BTN}
                        onClick={() => setFiltersOpen((prev) => !prev)}
                        aria-expanded={filtersOpen}
                        aria-label="Фильтры"
                    >
                        <SlidersHorizontal size={15} />
                        <span className="hidden sm:inline">Фильтры</span>
                        {panelCount > 0 && <span className="tabular-nums">{panelCount}</span>}
                    </button>
                    <button
                        type="button"
                        className={listCount ? TOOLBAR_BTN_ON : TOOLBAR_BTN}
                        onClick={() => setListOpen(true)}
                        aria-label="Поиск по списку ID или номеров ВУ"
                    >
                        <ClipboardList size={15} />
                        <span className="hidden sm:inline">Список ID/ВУ</span>
                        {listCount > 0 && <span className="tabular-nums">{formatInt(listCount)}</span>}
                    </button>
                </div>
            </div>

            {/* Что отобрано — видно, не открывая панель; чип снимается по одному. */}
            {chips.length > 0 && (
                <div className="mt-2 flex flex-wrap items-center gap-1.5">
                    {chips.map((chip) => (
                        <button
                            key={chip.key}
                            type="button"
                            onClick={() => updateFilters(chip.clear)}
                            title={`Убрать: ${chip.label}`}
                            className="group inline-flex max-w-full items-center gap-1.5 rounded-full bg-white px-2.5 py-1 text-[12.5px] text-slate-700 ring-1 ring-slate-200/80 transition hover:ring-slate-300 active:scale-[0.98]"
                        >
                            <span className="text-slate-400">{chip.name}</span>
                            <span className="truncate font-medium">{chip.label}</span>
                            <X size={12} className="shrink-0 text-slate-400 group-hover:text-slate-600" />
                        </button>
                    ))}
                    <button
                        type="button"
                        onClick={resetFilters}
                        className="px-1.5 text-[12.5px] text-slate-500 underline decoration-slate-300 underline-offset-2 transition hover:text-slate-700"
                    >
                        сбросить всё
                    </button>
                </div>
            )}

            {filtersOpen && (
                <div className={`${iosCard} mt-3 p-4`}>
                    {/* Листа здесь нет — он во вкладках над таблицей. Три списка и
                        четыре диапазона — одной сеткой на четыре колонки: так поля
                        обоих рядов стоят друг под другом. */}
                    <div className="grid gap-x-3 gap-y-4 sm:grid-cols-2 lg:grid-cols-4">
                        <label className="block space-y-1.5">
                            <span className={iosGroupLabel}>Город</span>
                            <CustomSelect value={filters.city} onChange={(value) => updateFilters({ city: value || '' })}
                                          options={listSelect(options.cities, 'Все города')} variant="ios" searchable
                                          textClassName="text-[13px] font-medium text-slate-800" ariaLabel="Город" />
                        </label>
                        <label className="block space-y-1.5">
                            <span className={iosGroupLabel}>Таксопарк</span>
                            <CustomSelect value={filters.park} onChange={(value) => updateFilters({ park: value || '' })}
                                          options={listSelect(options.parks, 'Все таксопарки')} variant="ios" searchable
                                          textClassName="text-[13px] font-medium text-slate-800" ariaLabel="Таксопарк" />
                        </label>
                        <label className="block space-y-1.5">
                            <span className={iosGroupLabel}>Приз</span>
                            <CustomSelect value={filters.prize} onChange={(value) => updateFilters({ prize: value || '' })}
                                          options={prizeOptions} variant="ios"
                                          textClassName="text-[13px] font-medium text-slate-800" ariaLabel="Приз" />
                        </label>
                        {RANGE_FIELDS.map(({ key, label }) => (
                            <div key={key} className="space-y-1.5">
                                <span className={iosGroupLabel}>{label}</span>
                                <div className="flex items-center gap-1.5">
                                    <RangeInput value={filters[`${key}_min`]} placeholder="от" label={`${label}: от`}
                                                invalid={Boolean(errors[`${key}_min`])}
                                                onChange={(value) => updateFilters({ [`${key}_min`]: value })} />
                                    <span className="text-slate-300">–</span>
                                    <RangeInput value={filters[`${key}_max`]} placeholder="до" label={`${label}: до`}
                                                invalid={Boolean(errors[`${key}_max`])}
                                                onChange={(value) => updateFilters({ [`${key}_max`]: value })} />
                                </div>
                            </div>
                        ))}
                    </div>
                    {Object.keys(errors).length > 0 && (
                        <p className="mt-2 text-[12px] text-rose-600">В диапазонах — только целые числа</p>
                    )}
                </div>
            )}

            {/* Список ID/ВУ: сколько нашлось и кто — нет, поимённо. Только для
                ответа на ТЕКУЩИЙ список: пока идёт запрос, старые цифры не видны. */}
            {listCount > 0 && listResult && listResult.count > 0 && (
                <div className="mt-3 flex flex-wrap items-baseline gap-x-2 gap-y-1 rounded-xl bg-white px-3.5 py-2.5 text-[13px] text-slate-700 ring-1 ring-slate-200/70">
                    <ClipboardList size={14} className="shrink-0 self-center text-slate-400" />
                    <span>
                        Список: {formatInt(listResult.count)} {plural(listResult.count, 'значение', 'значения', 'значений')}
                        {listResult.not_found_total > 0
                            ? ` · не нашлись ${formatInt(listResult.not_found_total)}:`
                            : ' · нашлись все'}
                    </span>
                    {listResult.not_found_total > 0 && (
                        <span className="min-w-0 break-all font-mono text-[12.5px] text-slate-600">
                            {listResult.not_found.slice(0, 12).join(', ')}
                            {listResult.not_found_total > 12 && ` и ещё ${formatInt(listResult.not_found_total - 12)}`}
                        </span>
                    )}
                    <span className="ml-auto flex gap-3">
                        <button type="button" className="text-blue-600 transition hover:text-blue-700"
                                onClick={() => setListOpen(true)}>Изменить</button>
                        <button type="button" className="text-slate-500 transition hover:text-slate-700"
                                onClick={() => updateFilters({ list: '' })}>Убрать</button>
                    </span>
                </div>
            )}

            {/* Вкладки листов — как в Excel. */}
            {sheets.length > 0 && (
                <SheetTabs sheets={sheets} counts={sheetCounts} total={sheetsTotal}
                           value={filters.zachet} onChange={pickSheet} />
            )}

            {/* Заголовок открытого листа — как имя листа в Excel — и под ним итог
                по всей выборке (п. 5 постановки). */}
            <div className="mb-3 mt-5 flex flex-wrap items-end justify-between gap-3 px-1">
                <div className="min-w-0">
                    <div className="flex items-center gap-2">
                        <span className="truncate text-[19px] font-bold leading-tight tracking-tight text-slate-900">
                            {allSheets ? (isSearching(filters) ? 'Поиск по всем листам' : 'Все листы') : filters.zachet}
                        </span>
                        {loading && <Loader2 size={15} className="shrink-0 animate-spin text-slate-400" />}
                    </div>
                <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-[13px] tabular-nums text-slate-500">
                    {totals ? (
                        <>
                            <span><b className="font-semibold text-slate-900">{formatInt(totals.rows)}</b> {plural(totals.rows, 'строка', 'строки', 'строк')}</span>
                            <span className="text-slate-300">·</span>
                            <span><b className="font-semibold text-slate-900">{formatInt(totals.drivers)}</b> {plural(totals.drivers, 'водитель', 'водителя', 'водителей')}</span>
                            <span className="text-slate-300">·</span>
                            <span>
                                призы <b className="font-semibold text-slate-900">{formatMoney(totals.prize_total)}</b>
                                {totals.prize_rows > 0 && ` (${formatInt(totals.prize_rows)})`}
                            </span>
                        </>
                    ) : <span className="text-slate-400">Считаем…</span>}
                </div>
                </div>
                {/* Размер страницы — на компьютере: на телефоне листают карточки,
                    и переключатель висел бы отдельной строкой. */}
                <div className="hidden items-center gap-2 sm:flex">
                    <span className="text-[12px] text-slate-400">На странице</span>
                    <IosSegmented
                        size="xs"
                        value={size}
                        onChange={(value) => { setSize(value); setPage(1); }}
                        options={PAGE_SIZES.map((value) => ({ value, label: String(value) }))}
                        ariaLabel="Строк на странице"
                    />
                </div>
            </div>

            {searchError && (
                <div className="mb-2 rounded-2xl bg-rose-50 px-4 py-3 text-[13px] text-rose-700 ring-1 ring-rose-200">{searchError}</div>
            )}

            {/* Компьютер: таблица. Рамка прокручивается по горизонтали — колонок
                до одиннадцати, и на ноутбуке им тесно. На «Всех листах» в порядке
                файла строки разделены заголовками листов. */}
            <div className={`${iosCard} hidden overflow-hidden md:block`}>
                <div className="thin-scroll overflow-x-auto">
                    <table className="w-full border-collapse text-left text-[13px]">
                        <thead>
                            <tr className="border-b border-slate-200/70 bg-slate-50/80 text-[11.5px] text-slate-500">
                                {showWeek && <SortHeader column="week" sort={sort} onSort={changeSort} className="pl-5" />}
                                {showZachetColumn && (
                                    <SortHeader column="zachet" sort={sort} onSort={changeSort} className={showWeek ? '' : 'pl-5'} />
                                )}
                                {/* w-px — колонка по ширине содержимого: иначе первой колонкой
                                    она забирала лишнее место, и номера стояли далеко от края. */}
                                <SortHeader column="position" sort={sort} onSort={changeSort} align="right"
                                            className={`w-px ${showWeek || showZachetColumn ? '' : 'pl-5'}`} />
                                <SortHeader column="driver" sort={sort} onSort={changeSort} />
                                <SortHeader column="prize" sort={sort} onSort={changeSort} />
                                <SortHeader column="amount" sort={sort} onSort={changeSort} align="right" />
                                <SortHeader column="trips" sort={sort} onSort={changeSort} align="right" />
                                <SortHeader column="city" sort={sort} onSort={changeSort} />
                                <SortHeader column="park" sort={sort} onSort={changeSort} />
                                <SortHeader column="license" sort={sort} onSort={changeSort} />
                                <SortHeader column="driver_id" sort={sort} onSort={changeSort} />
                                <th className="hidden w-8 2xl:table-cell" aria-hidden="true" />
                            </tr>
                        </thead>
                        <tbody className={`transition-opacity ${loading ? 'opacity-60' : ''}`}>
                            {tableRows.map((item, index) => {
                                if (item.type === 'group') {
                                    const stats = sheetCounts.get(item.zachet);
                                    const week = weekByStart.get(item.period_start);
                                    return (
                                        <tr key={`group-${item.key}`}>
                                            <td colSpan={columnCount}
                                                className={`bg-blue-50/60 px-4 py-2.5 shadow-[inset_3px_0_0_rgb(59,130,246)] ${index ? 'border-t border-slate-200' : ''}`}>
                                                <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                                                    <span className="grid h-7 w-7 shrink-0 place-items-center rounded-lg bg-white text-blue-600 ring-1 ring-blue-100 shadow-sm">
                                                        <GroupIcon name={item.zachet} />
                                                    </span>
                                                    <span className="text-[15px] font-bold text-slate-900">{item.zachet}</span>
                                                    {showWeek && (
                                                        <span className="rounded-md bg-white px-2 py-0.5 text-[12px] font-medium tabular-nums text-slate-600 ring-1 ring-slate-200/80">
                                                            {weekShort(item.period_start, week?.period_end)}
                                                        </span>
                                                    )}
                                                    {!showWeek && stats && (
                                                        <span className="text-[12.5px] tabular-nums text-slate-500">
                                                            {formatInt(stats.rows)} {plural(stats.rows, 'водитель', 'водителя', 'водителей')}
                                                            {stats.prize_rows > 0 && ` · призы ${formatMoney(stats.prize_total)}`}
                                                        </span>
                                                    )}
                                                    <button type="button" onClick={() => pickSheet(item.zachet)}
                                                            className="ml-auto inline-flex items-center gap-1 rounded-lg px-2 py-1 text-[12.5px] font-medium text-blue-600 transition hover:bg-white hover:text-blue-700">
                                                        Открыть лист <ArrowRight size={13} />
                                                    </button>
                                                </div>
                                            </td>
                                        </tr>
                                    );
                                }
                                const { row } = item;
                                const week = weekByStart.get(row.period_start);
                                const afterGroup = index > 0 && tableRows[index - 1].type === 'group';
                                return (
                                    <tr key={row.id} onClick={() => setDriver(row)}
                                        className={`group cursor-pointer transition hover:bg-slate-50/80 ${index && !afterGroup ? 'border-t border-slate-100' : ''}`}>
                                        {showWeek && (
                                            <td className="whitespace-nowrap py-2.5 pl-5 pr-2 tabular-nums text-slate-600 2xl:pr-3"
                                                title={week?.week_number ? `Неделя ${week.week_number}` : undefined}>
                                                {weekShort(row.period_start, week?.period_end)}
                                            </td>
                                        )}
                                        {showZachetColumn && (
                                            <td className={`max-w-[160px] truncate py-2.5 pr-2 text-slate-600 2xl:max-w-[220px] 2xl:pr-3 ${showWeek ? 'pl-2 2xl:pl-3' : 'pl-5'}`}
                                                title={row.zachet}>{row.zachet}</td>
                                        )}
                                        <td className={`${showWeek || showZachetColumn ? CELL : 'py-2.5 pl-5 pr-2 2xl:pr-3'} text-right font-semibold tabular-nums text-slate-900`}>
                                            {row.position}
                                        </td>
                                        <td className={`${CELL} whitespace-nowrap font-medium text-slate-900`}>{row.driver_name}</td>
                                        <td className={CELL}><PrizePill row={row} /></td>
                                        <td className={`${CELL} whitespace-nowrap text-right tabular-nums text-slate-800`}>{formatInt(row.amount)}</td>
                                        <td className={`${CELL} text-right tabular-nums text-slate-800`}>{formatInt(row.trips)}</td>
                                        <td className={`${CELL} whitespace-nowrap text-slate-700`}>{row.city}</td>
                                        <td className={`${CELL} max-w-[170px] truncate text-slate-700 2xl:max-w-[240px]`} title={row.park}>{row.park}</td>
                                        <td className={`${CELL} whitespace-nowrap tabular-nums text-slate-700`}>{row.license || '—'}</td>
                                        <td className={`${CELL} whitespace-nowrap`}>
                                            <CopyButton value={row.driver_id} label={`${row.driver_id} — скопировать`}
                                                        className="font-mono text-[12.5px] text-slate-600">
                                                {shortId(row.driver_id)}
                                            </CopyButton>
                                        </td>
                                        <td className="hidden pr-3 text-slate-300 transition group-hover:text-slate-400 2xl:table-cell">
                                            <ChevronRight size={16} />
                                        </td>
                                    </tr>
                                );
                            })}
                        </tbody>
                    </table>
                </div>
                {!rows.length && result && (
                    <div className="flex flex-col items-center px-4 py-12 text-center">
                        <SearchX size={22} className="text-slate-300" />
                        <div className="mt-2 text-[13.5px] text-slate-500">Ничего не нашлось</div>
                        {filtered && (
                            <button type="button" onClick={resetFilters}
                                    className="mt-1 text-[13px] text-blue-600 transition hover:text-blue-700">
                                Сбросить фильтры
                            </button>
                        )}
                    </div>
                )}
            </div>

            {/* Телефон: карточки той же выборки, листы — подписью группы, как в
                списках iOS; нажатие — карточка водителя. */}
            <div className="space-y-2 md:hidden">
                {tableRows.map((item) => {
                    if (item.type === 'group') {
                        const stats = sheetCounts.get(item.zachet);
                        const week = weekByStart.get(item.period_start);
                        return (
                            <div key={`group-${item.key}`} className="flex items-center gap-2.5 px-1 pb-0.5 pt-4">
                                <span className="grid h-7 w-7 shrink-0 place-items-center rounded-lg bg-blue-50 text-blue-600 ring-1 ring-blue-100">
                                    <GroupIcon name={item.zachet} />
                                </span>
                                <span className="min-w-0 truncate text-[15px] font-bold text-slate-900">{item.zachet}</span>
                                <span className="ml-auto shrink-0 text-[12px] tabular-nums text-slate-500">
                                    {showWeek
                                        ? weekShort(item.period_start, week?.period_end)
                                        : stats ? `${formatInt(stats.rows)} ${plural(stats.rows, 'водитель', 'водителя', 'водителей')}` : ''}
                                </span>
                            </div>
                        );
                    }
                    const { row } = item;
                    const week = weekByStart.get(row.period_start);
                    const subline = [
                        showZachetColumn ? row.zachet : '',
                        !grouped && weeks.length > 1 ? weekShort(row.period_start, week?.period_end) : '',
                    ].filter(Boolean).join(' · ');
                    return (
                        <button key={row.id} type="button" onClick={() => setDriver(row)}
                                className={`${iosCard} block w-full p-3.5 text-left transition active:scale-[0.99]`}>
                            <div className="flex items-start justify-between gap-2">
                                <div className="min-w-0 grow basis-0">
                                    <div className="truncate text-[15px] font-semibold text-slate-900">{row.driver_name}</div>
                                    {subline && <div className="mt-0.5 truncate text-[12.5px] text-slate-500">{subline}</div>}
                                </div>
                                <span className="shrink-0 rounded-lg bg-slate-100 px-2 py-1 text-[13px] font-bold tabular-nums text-slate-700">
                                    {row.position}
                                </span>
                            </div>
                            <div className="mt-2.5 flex flex-wrap items-center gap-x-3 gap-y-1.5 text-[13px]">
                                <span className="font-semibold tabular-nums text-slate-900">{formatMoney(row.amount)}</span>
                                <span className="tabular-nums text-slate-500">
                                    {formatInt(row.trips)} {plural(row.trips, 'поездка', 'поездки', 'поездок')}
                                </span>
                                {row.has_prize && <PrizePill row={row} />}
                            </div>
                            <div className="mt-2 truncate text-[12.5px] text-slate-500">{row.city} · {row.park}</div>
                        </button>
                    );
                })}
                {!rows.length && result && (
                    <div className={`${iosCard} flex flex-col items-center px-4 py-12 text-center`}>
                        <SearchX size={22} className="text-slate-300" />
                        <div className="mt-2 text-[13.5px] text-slate-500">Ничего не нашлось</div>
                    </div>
                )}
            </div>

            <div className="mt-3">
                <IosPager
                    page={page}
                    pageCount={pageCount}
                    total={formatInt(total)}
                    from={formatInt(total ? (page - 1) * size + 1 : 0)}
                    to={formatInt(Math.min(page * size, total))}
                    onPage={(next) => setPage(Math.min(Math.max(1, next), pageCount))}
                />
            </div>

            {modals}
        </>,
    );
};

export default BaigaView;
