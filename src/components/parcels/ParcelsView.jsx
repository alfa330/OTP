import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import { Download, Loader2, Package, Plus, RefreshCw, Search, SlidersHorizontal, X } from 'lucide-react';
import {
    APPLE_FONT, iosBtnGhost, iosBtnPrimary, iosBtnSecondary, iosCard, iosInput, iosGroupLabel,
} from '../ui/ios';
import CustomSelect from '../ui/CustomSelect';
import { IosDateRangeCalendar, IosDateRangePicker, isoDate, rangeLabel } from '../ui/DateRangePicker';
import ParcelCard from './ParcelCard';
import ParcelForm from './ParcelForm';
import {
    EXPORT_MAX_DAYS, STATE_FILTERS, daysInOffice, driverAccountUrl, exportFileName, fmtDate,
    fmtPhone, isStale, kindMeta, pluralDays, rangeDays, rowTone, shiftDaysBack, statusMeta,
    todayISO, toneEdge, tonePill, toneRow, toneText,
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
 * Про цвет. Строка окрашивается ПО СТАТУСУ целиком (просьба владельца
 * 25.08.2026) — тем же приёмом, что строки офисов в вики. Оттенков четыре, а не
 * три: «в офисе» делится на «лежит» и «залежалась», потому что раздел про
 * невостребованное, и вопрос «что пора разбирать» в нём главный. Палитра и
 * правило выбора — в parcelMeta.js, чтобы таблица, карточки на телефоне,
 * бейджи и легенда красились из ОДНОГО места.
 *
 * Легенда над таблицей — она же фильтр по статусу: цветовую кодировку человек
 * читает без обучения, а «покажи только залежавшиеся» находится одним
 * нажатием. Две отдельные полосы (легенда и фильтр) были бы двумя строками про
 * одно и то же.
 */

const PAGE_SIZE = 50;

// Сколько ждать после последней буквы, прежде чем идти на сервер. 300 мс —
// тот же порядок, что в остальных поисках портала: набранное целиком слово
// уходит одним запросом, а не по букве.
const SEARCH_DEBOUNCE_MS = 300;

const EMPTY_FILTERS = { city: '', office_id: null, manager_id: null, date_from: '', date_to: '' };

/* Пресеты диапазона. Объявлены МОДУЛЬНОЙ константой: инлайновый литерал —
   новый массив на каждый рендер, а он уходит пропсом в пикер.
   «Весь период» пикер добавляет сам, поэтому здесь только рабочие окна: за
   месяц посылку ищут чаще всего, «залежавшиеся» начинаются после 30 дней. */
const shiftDays = (days) => {
    const value = new Date();
    value.setDate(value.getDate() - days);
    return isoDate(value);
};

/* Вид чипа диапазона — ОДИН В ОДИН с ios-вариантом CustomSelect (белое поле,
   ring-1, px-3 py-2, 12.5px, тот же мягкий контур). Иначе «Дата приёма» стоит в
   ряду трёх селекторов кнопкой по своей ширине и выбивается из строки.
   `[&>span]:flex-1` нужен, потому что triggerClassName заменяет класс кнопки
   целиком: без него подпись не растягивается и шеврон уезжает к тексту.
   Состояния свои — открытому поповеру кнопка остаётся в фокусе, поэтому кольцо
   на `focus`, как у эталона в OfficeDayModal. */
const DATE_TRIGGER = 'flex w-full items-center gap-2 rounded-xl bg-white px-3 py-2 '
    + 'text-left text-[12.5px] font-medium text-slate-700 ring-1 ring-slate-200/70 '
    + 'shadow-[0_1px_2px_rgba(15,23,42,0.04)] transition-all hover:bg-slate-50 '
    + 'active:scale-[0.99] focus:outline-none focus:ring-2 focus:ring-blue-500/60 '
    + '[&>span]:flex-1 [&>span]:text-left [&>span]:truncate';

const DATE_PRESETS = [
    { label: 'Сегодня', range: () => ({ from: isoDate(new Date()), to: isoDate(new Date()) }) },
    { label: 'Неделя', range: () => ({ from: shiftDays(6), to: isoDate(new Date()) }) },
    { label: 'Месяц', range: () => ({ from: shiftDays(29), to: isoDate(new Date()) }) },
];

/* Пресеты пикера выгрузки. «Весь период» здесь нет и быть не может: период у
   файла обязателен и не длиннее EXPORT_MAX_DAYS суток. Последний пресет ровно
   в потолок — им же человек и узнаёт, сколько максимум можно взять за раз.
   Отсчёт от сегодняшнего дня по Алматы (`todayISO`), а не от `new Date()`:
   у сотрудника в другом поясе иначе поехала бы граница на сутки. */
const EXPORT_PRESETS = [
    { label: 'Сегодня', range: () => ({ from: todayISO(), to: todayISO() }) },
    { label: 'Неделя', range: () => ({ from: shiftDaysBack(todayISO(), 6), to: todayISO() }) },
    {
        label: `${EXPORT_MAX_DAYS} дней`,
        range: () => ({ from: shiftDaysBack(todayISO(), EXPORT_MAX_DAYS - 1), to: todayISO() }),
    },
];


/* Водитель в строке. Цвет приходит пропсом, а не берётся из slate: строка
   залита по статусу, и «серый по умолчанию» на янтаре и зелени выглядит
   выцветшим.
 *
 * ФИО ведёт в аккаунт водителя во Флите, телефон — звонок. Оба щелчка гасят
 * всплытие: иначе поверх ссылки открывалась бы ещё и карточка посылки, и человек
 * получал бы новую вкладку И модалку на один щелчок. Подчёркивание проявляется
 * при наведении — постоянное превращало бы колонку в частокол линий. */
const DriverCell = ({ parcel, text }) => {
    const account = driverAccountUrl(parcel);
    const name = parcel.driver_name || '—';
    return (
        <>
            {account ? (
                <a
                    href={account}
                    target="_blank"
                    rel="noopener noreferrer"
                    onClick={(event) => event.stopPropagation()}
                    title="Открыть аккаунт водителя во Флите"
                    className={`block truncate underline decoration-transparent underline-offset-2 transition hover:decoration-inherit ${text.main}`}
                >
                    {name}
                </a>
            ) : (
                <div className={`truncate ${text.main}`}>{name}</div>
            )}
            {parcel.driver_phone && (
                <a
                    href={`tel:${parcel.driver_phone}`}
                    onClick={(event) => event.stopPropagation()}
                    className={`tabular-nums text-[12.5px] underline decoration-transparent underline-offset-2 transition hover:decoration-inherit ${text.body}`}
                >
                    {fmtPhone(parcel.driver_phone)}
                </a>
            )}
        </>
    );
};

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
    const [downloading, setDownloading] = useState(false);

    /* Пикер периода выгрузки. Период — ОБЯЗАТЕЛЬНЫЙ параметр файла, поэтому он
       живёт своим состоянием, а не берётся из фильтра «Дата приёма»: фильтр
       человек мог не трогать вовсе, а выгрузка без рамки не собирается.
       По умолчанию — максимальное окно, заканчивающееся сегодня: чаще всего
       спрашивают «что было за месяц», и это же показывает потолок. */
    const [exportOpen, setExportOpen] = useState(false);
    const [exportRange, setExportRange] = useState(() => {
        const today = todayISO();
        return { from: shiftDaysBack(today, EXPORT_MAX_DAYS - 1), to: today };
    });
    const exportRef = useRef(null);

    /* showToast приходит новой функцией на каждый рендер App — известная
       ловушка портала. Держим её в ref, чтобы она не попала в зависимости
       колбэка выгрузки и не пересобирала его вместе со всем, что от него
       зависит. */
    const toastRef = useRef(showToast);
    useEffect(() => { toastRef.current = showToast; }, [showToast]);

    /* Клик мимо и Esc закрывают панель выгрузки — как у эталонного пикера
       табло СЗоВ. Слушаем `mousedown`, а не `click`: прокрутка колесом внутри
       панели тогда не считается внешней и не гасит её. */
    useEffect(() => {
        if (!exportOpen) return undefined;
        const onDown = (event) => {
            if (exportRef.current && !exportRef.current.contains(event.target)) {
                setExportOpen(false);
            }
        };
        const onKey = (event) => { if (event.key === 'Escape') setExportOpen(false); };
        document.addEventListener('mousedown', onDown);
        document.addEventListener('keydown', onKey);
        return () => {
            document.removeEventListener('mousedown', onDown);
            document.removeEventListener('keydown', onKey);
        };
    }, [exportOpen]);

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

    /* Что именно отобрано — ОДНОЙ функцией на список и на выгрузку.
     *
     * Условий семь, и живут они в трёх местах состояния: сегмент статуса,
     * придержанный поиск и объект фильтров. Пока их собирал только `load`, это
     * было незаметно; выгрузке пришлось бы повторить всю семёрку своей строкой —
     * и разошлись бы они на первой же новой фильтрации, а человек увидел бы в
     * файле не то, что на экране. Тот же приём применён и на сервере: список,
     * счётчики и выгрузка ходят через один `_filter_clause`.
     *
     * `limit`/`offset` сюда не входят: это не отбор, а страница, и у выгрузки
     * её нет — иначе файл молча обрезался бы полусотней строк.
     */
    const selection = useMemo(() => {
        const params = new URLSearchParams();
        const status = STATE_FILTERS.find((item) => item.key === state)?.status;
        if (status) params.set('status', status);
        if (query) params.set('q', query);
        if (filters.city) params.set('city', filters.city);
        if (filters.office_id) params.set('office_id', String(filters.office_id));
        if (filters.manager_id) params.set('manager_id', String(filters.manager_id));
        if (filters.date_from) params.set('date_from', filters.date_from);
        if (filters.date_to) params.set('date_to', filters.date_to);
        return params;
    }, [state, query, filters]);

    // `from` приходит аргументом, а не из состояния: иначе смена фильтра при
    // догруженных страницах успевала уйти на сервер со СТАРЫМ смещением — и в
    // список подмешивалась порция от прежнего запроса.
    const load = useCallback(async ({ append = false, from = 0 } = {}) => {
        const ticket = requestRef.current + 1;
        requestRef.current = ticket;
        setLoading(true);
        setLoadError('');
        const params = new URLSearchParams(selection);
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
    }, [apiBaseUrl, headers, selection]);

    // Смена условий всегда начинает список заново. Догрузка следующей порции —
    // отдельное действие кнопки, а не состояние в зависимостях эффекта.
    useEffect(() => {
        load({ append: false, from: 0 });
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [state, query, filters, apiBaseUrl]);

    /* Выгрузка в Excel (задача #257, период выбирается пикером с 01.09.2026).
     *
     * Файл забираем axios'ом, а не ссылкой <a href>: портал авторизуется
     * заголовком, а ссылка заголовков не несёт — вместо книги приехала бы
     * страница входа. Отдаём его браузеру временной ссылкой на blob, как во
     * всех остальных выгрузках портала.
     *
     * Уходит `selection` — тот же отбор, что виден на экране, — но ДАТЫ в нём
     * замещаются выбранным периодом: человек только что назвал его руками, и
     * экранный фильтр «Дата приёма» здесь не при чём. Номера страницы нет: в
     * файл идёт весь период, а не полсотни загруженных строк.
     */
    const download = useCallback((from, to) => {
        setExportOpen(false);
        setDownloading(true);
        const params = new URLSearchParams(selection);
        params.set('date_from', from);
        params.set('date_to', to);
        axios.get(`${apiBaseUrl}/api/parcels/export?${params.toString()}`,
            { headers: headers(), responseType: 'blob' })
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
            .catch(async (error) => {
                // Ошибку сервер прислал JSON-ом, а мы просили blob — разворачиваем,
                // иначе в тост уехало бы «[object Blob]».
                let message = 'Не удалось собрать выгрузку';
                try {
                    const text = await error?.response?.data?.text?.();
                    message = JSON.parse(text || '{}').error || message;
                } catch (_) { /* пусто — останется общая фраза */ }
                toastRef.current?.(message, 'error');
            })
            .finally(() => setDownloading(false));
    }, [apiBaseUrl, headers, selection]);

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

    /* Что делать после сохранения — заведения, правки или смены статуса.
     *
     * Два шага, и оба нужны. Сначала кладём свежую запись на место старой:
     * строка перекрашивается и статус меняется мгновенно, без мигания списка.
     * Потом перезапрашиваем список — потому что счётчики «Все / В офисе / …»,
     * общее число и сам СОСТАВ выборки считает сервер по текущим фильтрам, и
     * посчитать их у себя нельзя: посылка, у которой сменился статус, может
     * вообще выпасть из выбранного сегмента.
     *
     * Раньше второго шага не было — и счётчики стояли на прежних числах до
     * перезагрузки страницы, хотя строка уже показывала новый статус.
     */
    const applySaved = useCallback((saved) => {
        if (saved) {
            setItems((prev) => {
                const known = prev.some((item) => item.id === saved.id);
                return known
                    ? prev.map((item) => (item.id === saved.id ? saved : item))
                    : [saved, ...prev];
            });
            setOpened((prev) => (prev && prev.id === saved.id ? saved : prev));
        }
        load({ append: false, from: 0 });
        loadFilters();
    }, [load, loadFilters]);

    /* Удаление — тот же порядок: убрали строку, пересчитали сводку. */
    const applyDeleted = useCallback((id) => {
        setItems((prev) => prev.filter((item) => item.id !== id));
        setTotal((prev) => Math.max(0, prev - 1));
        load({ append: false, from: 0 });
        loadFilters();
    }, [load, loadFilters]);

    /* Длина выбранного периода и подсказка под календарём. Считаем здесь, а не
       в разметке: и «Подтвердить», и строка под ней читают одно число.
       Потолок сторожит сервер — здесь он лишь гасит кнопку заранее, чтобы
       человек узнал о нём до ожидания, а не из ошибки после. */
    const exportDays = rangeDays(exportRange.from, exportRange.to);
    const exportTooLong = exportDays > EXPORT_MAX_DAYS;
    const exportHint = exportTooLong
        ? `Максимум ${EXPORT_MAX_DAYS} суток за раз — выберите период короче`
        : (exportDays
            ? `${rangeLabel(exportRange.from, exportRange.to)} · ${pluralDays(exportDays)} по дате приёма`
            : 'Выберите период — по дате приёма посылки');

    /* Сколько на экране залежавшихся. Считаем по загруженной странице, а не
       запросом: это подсказка «есть чем заняться», а не число из отчёта, и
       ради неё ходить на сервер незачем. Поэтому и подпись без «из N». */
    const staleCount = useMemo(
        () => items.reduce((sum, parcel) => sum + (isStale(parcel) ? 1 : 0), 0),
        [items],
    );

    /* Человекочитаемые чипы активных фильтров. Собираются здесь, а не в
       разметке: каждому нужен и текст, и способ снять именно его. */
    const activeFilterChips = useMemo(() => {
        const chips = [];
        if (filters.city) {
            chips.push({
                key: 'city', name: 'Город', label: filters.city,
                clear: () => setFilters((prev) => ({ ...prev, city: '', office_id: null })),
            });
        }
        if (filters.office_id) {
            const office = offices.find((item) => item.id === filters.office_id);
            chips.push({
                key: 'office', name: 'Офис', label: office?.name || `№${filters.office_id}`,
                clear: () => setFilters((prev) => ({ ...prev, office_id: null })),
            });
        }
        if (filters.manager_id) {
            const manager = managers.find((item) => item.id === filters.manager_id);
            chips.push({
                key: 'manager', name: 'Менеджер', label: manager?.name || `№${filters.manager_id}`,
                clear: () => setFilters((prev) => ({ ...prev, manager_id: null })),
            });
        }
        if (filters.date_from || filters.date_to) {
            chips.push({
                key: 'dates', name: 'Приняты',
                label: rangeLabel(filters.date_from || '', filters.date_to || ''),
                clear: () => setFilters((prev) => ({ ...prev, date_from: '', date_to: '' })),
            });
        }
        return chips;
    }, [filters, managers, offices]);

    const officeOptions = useMemo(() => {
        const list = filters.city
            ? offices.filter((office) => office.city === filters.city)
            : offices;
        return list.map((office) => ({
            value: office.id,
            label: filters.city ? office.name : `${office.city} · ${office.name}`,
        }));
    }, [filters.city, offices]);

    const filtersActive = activeFilterChips.length;

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
                {/* Ряд действий переносится: с появлением «Выгрузить» (задача
                    #257) три кнопки перестали помещаться в строку уже на 375 px,
                    и «Добавить посылку» ломалась на две строки внутри себя —
                    ряд получался рваным (40/40/61). Пусть лучше кнопка целиком
                    уедет на свою строку, чем разорвётся её подпись. */}
                <div className="flex shrink-0 flex-wrap items-center gap-2">
                    <button
                        type="button"
                        className={iosBtnGhost}
                        onClick={() => { load({ append: false, from: 0 }); loadFilters(); }}
                        aria-label="Обновить"
                    >
                        <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
                    </button>
                    {/* Выгрузка стоит ДО гейта canEdit: реестр заводили ради
                        оператора СЗоВ, он раздел только читает — а «сохранить
                        то, что вижу» ничем не отличается от «посмотреть».
                        Внутри гейта кнопку видели бы одни фронт-офисы.

                        Нажатие не качает файл сразу, а раскрывает пикер периода:
                        период у выгрузки обязателен и не длиннее месяца. Форма
                        пикера — эталонная, как у выгрузки табло СЗоВ: календарь
                        из общего примитива, своя кнопка «Подтвердить» под ним и
                        строка-подсказка. На пустом отборе кнопку НЕ гасим:
                        период выбирают свой, и то, что сейчас на экране пусто,
                        про него ничего не говорит. */}
                    <div ref={exportRef} className="relative shrink-0">
                        <button
                            type="button"
                            className={`${iosBtnSecondary} ${exportOpen ? 'bg-slate-200 text-slate-900' : ''}`}
                            onClick={() => setExportOpen((value) => !value)}
                            disabled={downloading}
                            title="Выгрузить в Excel за выбранный период"
                        >
                            {downloading
                                ? <Loader2 size={15} className="animate-spin" />
                                : <Download size={15} />}
                            {downloading ? 'Готовим файл…' : 'Выгрузить'}
                        </button>
                        {exportOpen && (
                            /* От sm панель прижата к ПРАВОМУ краю кнопки: она
                               стоит у правого края шапки, и раскрытие влево
                               увело бы календарь за экран.
                               На телефоне — наоборот, влево: там кнопка сама
                               прижата к левому краю, и панель шириной 268 px
                               при правой привязке уезжала за край на 83 px
                               (замер на 390 px, 01.09.2026). Прокруткой это не
                               достать — страница по горизонтали не едет.
                               Сдвиг на 48 px выводит панель к краю содержимого
                               раздела, а не к краю кнопки: от её левого края
                               (58 px) панель иначе не помещалась на 320 px —
                               вылезала правым краем на 6 px. */
                            <div className="absolute -left-12 top-full z-[60] mt-2 sm:left-auto sm:right-0">
                                <IosDateRangeCalendar
                                    from={exportRange.from}
                                    to={exportRange.to}
                                    presets={EXPORT_PRESETS}
                                    onChange={(next) => setExportRange({
                                        from: next.from || next.to,
                                        to: next.to || next.from,
                                    })}
                                    footer={(
                                        <div className="mt-2.5 border-t border-slate-100 pt-2.5">
                                            <button
                                                type="button"
                                                className={`${iosBtnPrimary} w-full`}
                                                disabled={!exportDays || exportTooLong}
                                                onClick={() => download(exportRange.from, exportRange.to)}
                                            >
                                                <Download size={15} />
                                                Подтвердить
                                            </button>
                                            <p className={`mt-1.5 text-center text-[11px] ${
                                                exportTooLong ? 'text-rose-500' : 'text-slate-400'}`}>
                                                {exportHint}
                                            </p>
                                        </div>
                                    )}
                                />
                            </div>
                        )}
                    </div>
                    {canEdit && (
                        <button
                            type="button"
                            className={`${iosBtnPrimary} flex-1 whitespace-nowrap sm:flex-none`}
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
                    /* На телефоне кнопка жмётся к своему тексту (self-start), а не
                       растягивается на всю строку: широкая пустая кнопка читается
                       как главное действие экрана, а это не так. */
                    className={`${filtersActive ? iosBtnPrimary : iosBtnSecondary} shrink-0 self-start sm:self-auto`}
                    onClick={() => setFiltersOpen((prev) => !prev)}
                >
                    <SlidersHorizontal size={15} />
                    Фильтры
                    {filtersActive > 0 && <span className="tabular-nums">· {filtersActive}</span>}
                </button>
            </div>

            {/* Полоса-легенда, она же фильтр по статусу. Залитая полоса, а не
                россыпь чипов по белому полю: три подписи вразброс читаются как
                случайные слова над таблицей, а не как ключ к её цветам. Кружок
                берётся из той же палитры, что заливка строки — легенда учит
                читать цвет, и разойдись они на полтона, мешала бы этому.
                Счётчики считает сервер по ТЕКУЩИМ фильтрам без учёта статуса,
                поэтому на сегменте «В офисе» видно и сколько уже передали. */}
            <div className="mt-3 flex flex-wrap items-center gap-1 rounded-xl bg-slate-100 px-2 py-1.5">
                {STATE_FILTERS.map((item) => {
                    const count = item.key === 'all' ? counters.all : counters[item.key];
                    const active = state === item.key;
                    return (
                        <button
                            key={item.key}
                            type="button"
                            onClick={() => setState(item.key)}
                            className={`flex items-center gap-1.5 rounded-lg px-2.5 py-1 text-[12.5px] transition ${
                                active
                                    ? 'bg-slate-900 font-medium text-white'
                                    : 'text-slate-700 hover:bg-white'
                            }`}
                        >
                            {item.tone && (
                                <span className={`h-2 w-2 shrink-0 rounded-full ${tonePill(item.tone).dot}`} />
                            )}
                            {item.label}
                            {count !== undefined && (
                                <span className="font-semibold tabular-nums">{count}</span>
                            )}
                        </button>
                    );
                })}
                {/* «Залежались» — не статус, а срез внутри «в офисе», поэтому стоит
                    справкой в конце полосы, а не четвёртым сегментом: сегменты
                    обязаны складываться в «Все». */}
                {staleCount > 0 && (
                    <span className="ml-auto flex items-center gap-1.5 px-1.5 text-[12px] text-amber-700">
                        <span className={`h-2 w-2 shrink-0 rounded-full ${tonePill('stale').dot}`} />
                        залежались
                        <span className="font-semibold tabular-nums">{staleCount}</span>
                    </span>
                )}
            </div>

            {/* Что отобрано — видно, не открывая панель. Раньше набор фильтров
                прятался за кнопкой и наружу торчало только число: человек видел
                «Фильтры · 2» и не помнил, какие именно. Чип снимается крестиком
                по одному, а не «сбросить всё». */}
            {filtersActive > 0 && (
                <div className="mt-2 flex flex-wrap items-center gap-1.5">
                    {activeFilterChips.map((chip) => (
                        <button
                            key={chip.key}
                            type="button"
                            onClick={chip.clear}
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
                        onClick={() => setFilters(EMPTY_FILTERS)}
                        className="px-1.5 text-[12.5px] text-slate-500 underline decoration-slate-300 underline-offset-2 transition hover:text-slate-700"
                    >
                        сбросить всё
                    </button>
                </div>
            )}

            {filtersOpen && (
                <div className={`${iosCard} mt-3 p-3.5`}>
                    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                        <label className="block space-y-1.5">
                            <span className={iosGroupLabel}>Город</span>
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
                            <span className={iosGroupLabel}>Офис</span>
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
                            <span className={iosGroupLabel}>Менеджер</span>
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
                        {/* Дата приёма — ОДИН чип с диапазоном вместо двух системных
                            полей. Раскрытый системный календарь рисует браузер: своя
                            шапка, свои кнопки, чужая деталь рядом с rounded-2xl.
                            Здесь тот же примитив, что в аналитике вики и в чатах. */}
                        {/* Обёртка и подпись — как у трёх соседей: у них подпись
                            строчный <span> внутри <label>, и `block` здесь поднимал
                            «ДАТА ПРИЁМА» на пару пикселей выше остальных. */}
                        <label className="block space-y-1.5">
                            <span className={iosGroupLabel}>Дата приёма</span>
                            <IosDateRangePicker
                                from={filters.date_from || ''}
                                to={filters.date_to || ''}
                                max={isoDate(new Date())}
                                onChange={({ from, to }) => setFilters((prev) => ({
                                    ...prev, date_from: from || '', date_to: to || '',
                                }))}
                                presets={DATE_PRESETS}
                                triggerClassName={DATE_TRIGGER}
                            />
                        </label>
                    </div>
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
                <table className="w-full min-w-[940px] border-collapse text-[13.5px]">
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
                        {items.map((parcel, index) => {
                            const lying = daysInOffice(parcel);
                            const tone = rowTone(parcel);
                            const text = toneText(tone);
                            const pill = tonePill(tone);
                            return (
                                <tr
                                    key={parcel.id}
                                    onClick={() => openParcel(parcel)}
                                    /* Волосок затемнением, а не серой линией: он обязан
                                       читаться и на янтарной строке, и на зелёной.
                                       Первой строке он не нужен — её сверху держит
                                       граница шапки. Заливка идёт по статусу, поэтому
                                       hover не перекрашивает строку, а притемняет её:
                                       hover:bg-slate-50 стирал бы состояние. */
                                    /* Высота на СТРОКЕ, а не в отступах ячейки:
                                       у закрытых записей под бейджем стоит имя
                                       сотрудника, и ряды получались рваными
                                       (62/62/65/65) — на залитых строках это
                                       видно сразу. */
                                    className={`h-[64px] cursor-pointer transition hover:brightness-[0.97] ${
                                        index > 0 ? 'border-t border-slate-900/[0.06]' : ''
                                    } ${toneRow(tone)}`}
                                >
                                    <td className="px-3.5 py-2.5 align-top whitespace-nowrap">
                                        <div className={`tabular-nums ${text.main}`}>{fmtDate(parcel.received_on)}</div>
                                        {lying !== null && (
                                            <div className={`text-[12px] leading-4 ${text.meta} ${tone === 'stale' ? 'font-medium' : ''}`}>
                                                лежит {pluralDays(lying)}
                                            </div>
                                        )}
                                    </td>
                                    <td className="px-3.5 py-2.5 align-top">
                                        <div className={text.main}>{parcel.city}</div>
                                        <div className={`text-[12.5px] ${text.body}`}>{parcel.office_name || '—'}</div>
                                    </td>
                                    <td className="max-w-[220px] px-3.5 py-2.5 align-top">
                                        <DriverCell parcel={parcel} text={text} />
                                    </td>
                                    <td className="max-w-[260px] px-3.5 py-2.5 align-top">
                                        <div className={text.main}>{kindMeta(parcel.kind).label}</div>
                                        <div className={`truncate text-[12.5px] ${text.body}`}>{parcel.description}</div>
                                    </td>
                                    <td className={`max-w-[200px] px-3.5 py-2.5 align-top text-[12.5px] ${text.body}`}>
                                        {parcel.sender && <div className="truncate">от {parcel.sender}</div>}
                                        {parcel.recipient && <div className="truncate">для {parcel.recipient}</div>}
                                        {!parcel.sender && !parcel.recipient && <span className={text.meta}>—</span>}
                                    </td>
                                    <td className="px-3.5 py-2.5 align-top whitespace-nowrap">
                                        {/* Бейдж, а не просто текст: в залитой строке
                                            подпись сливается с фоном, а плашка держит
                                            статус читаемым и повторяет цвет строки —
                                            то же решение, что у офисов в вики. */}
                                        <span className={`inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[12px] font-medium ${pill.fill}`}>
                                            <span className={`h-1.5 w-1.5 rounded-full ${pill.dot}`} />
                                            {statusMeta(parcel.status).label}
                                        </span>
                                        {parcel.status !== 'in_office' && parcel.status_changed_by_name && (
                                            <div className={`mt-0.5 text-[12px] leading-4 ${text.meta}`}>
                                                {parcel.status_changed_by_name}
                                            </div>
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

            {/* Телефон: кант слева вместо заливки всей карточки. Двадцать
                полностью залитых карточек читаются как тревога, а не как
                состояние — то же решение, что у карточек офисов в вики. */}
            <div className="mt-4 space-y-2 md:hidden">
                {items.map((parcel) => {
                    const lying = daysInOffice(parcel);
                    const tone = rowTone(parcel);
                    const pill = tonePill(tone);
                    return (
                        <button
                            key={parcel.id}
                            type="button"
                            onClick={() => openParcel(parcel)}
                            className={`${iosCard} relative w-full overflow-hidden p-3.5 pl-4 text-left transition active:scale-[0.99] before:absolute before:inset-y-0 before:left-0 before:w-[3px] ${toneEdge(tone)}`}
                        >
                            <div className="flex items-baseline justify-between gap-2">
                                <span className="truncate text-[14px] font-medium text-slate-900">
                                    {parcel.driver_name || '—'}
                                </span>
                                <span className={`shrink-0 rounded-full px-2 py-0.5 text-[11.5px] font-medium ${pill.fill}`}>
                                    {statusMeta(parcel.status).label}
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
                                    <span className={tone === 'stale' ? 'font-medium text-amber-700' : ''}>
                                        лежит {pluralDays(lying)}
                                    </span>
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
                onDeleted={applyDeleted}
                showToast={showToast}
            />
        </div>
    );
};

export default ParcelsView;
