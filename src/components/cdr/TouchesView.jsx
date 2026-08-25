import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import {
    Download, Filter, Loader2, Phone, PhoneIncoming, PhoneMissed,
    PhoneOutgoing, PlugZap, RefreshCw, Search, X,
} from 'lucide-react';

import {
    APPLE_FONT, iosBtnGhost, iosBtnPrimary, iosBtnSecondary, iosCard, iosGroupLabel,
    iosInput, IosPager, IosSegmented,
} from '../ui/ios';
import CustomSelect from '../ui/CustomSelect';
import { IosDateRangePicker, isoDate, rangeLabel } from '../ui/DateRangePicker';
import {
    exportFileName, hms, hours, percent, prettyPhone, resultTone,
    shortDay, shortTime, silence,
} from './touchMeta';

/* Раздел «Касания» — звонки отдела продаж из CDR АТС FreePBX.
 *
 * Экран подчинён одному действию: выбрать период и получить по нему файл.
 * Поэтому период стоит первым и всегда виден, а всё остальное — фильтры,
 * разрезы, таблица — под ним и по требованию.
 *
 * ПОЧЕМУ ЗДЕСЬ ЕСТЬ ПОЛОСА ПРОГРЕССА. Сутки отдела продаж — это около 23 тысяч
 * строк на станции, и первый заход за новым периодом их выкачивает: месяц идёт
 * минутами. Молчащая кнопка «Выгрузить» на пять минут — это сломанная кнопка,
 * поэтому раздел честно показывает «сутки 12 из 31» и разрешает уйти: работа
 * идёт на сервере, выкачанное остаётся в базе, и второй заход за тот же период
 * мгновенный.
 *
 * ПОЧЕМУ ТАБЛИЦА, А НЕ ТОЛЬКО КНОПКА. Раз данные всё равно легли в базу, экран
 * показывает то же, что уедет в файл. Расхождение между экраном и выгрузкой —
 * самый дорогой сорт расхождения: его замечают уже в переписке с заказчиком.
 */

const POLL_MS = 3000;
const SEARCH_DEBOUNCE_MS = 350;
const PAGE_SIZE = 50;

const errText = (error, fallback) => (
    error?.response?.data?.error || error?.message || fallback);

const dayShift = (days) => isoDate(new Date(Date.now() - days * 864e5));

const monthRange = (offset) => {
    const now = new Date();
    const first = new Date(now.getFullYear(), now.getMonth() - offset, 1);
    const last = new Date(now.getFullYear(), now.getMonth() - offset + 1, 0);
    const today = isoDate(now);
    const to = isoDate(last);
    return { from: isoDate(first), to: to > today ? today : to };
};

/* Пресеты объявлены МОДУЛЬНОЙ константой: инлайновый литерал — это новый массив
 * на каждый рендер, а он уходит пропсом в пикер. */
const DATE_PRESETS = [
    { label: 'Сегодня', range: () => ({ from: dayShift(0), to: dayShift(0) }) },
    { label: 'Вчера', range: () => ({ from: dayShift(1), to: dayShift(1) }) },
    { label: '7 дней', range: () => ({ from: dayShift(6), to: dayShift(0) }) },
    { label: '30 дней', range: () => ({ from: dayShift(29), to: dayShift(0) }) },
    { label: 'Этот месяц', range: () => monthRange(0) },
    { label: 'Прошлый месяц', range: () => monthRange(1) },
];

const TYPE_SEGMENTS = [
    { value: '', label: 'Все' },
    { value: 'Исходящий', label: 'Исходящие', icon: <PhoneOutgoing size={13} /> },
    { value: 'Входящий', label: 'Входящие', icon: <PhoneIncoming size={13} /> },
    { value: 'Входящий (не приняли)', label: 'Не приняли', icon: <PhoneMissed size={13} /> },
];

const TABS = [
    { value: 'touches', label: 'Касания' },
    { value: 'operators', label: 'Операторы' },
    { value: 'daily', label: 'По дням' },
];

const EMPTY_FILTERS = { result: '', ext: '', queue: '', talkedOnly: false };

const Metric = ({ label, value, hint }) => (
    <div className={`${iosCard} px-3.5 py-3`}>
        <div className="text-[11px] font-semibold uppercase tracking-wider text-slate-400">{label}</div>
        <div className="mt-1 text-[21px] font-semibold tabular-nums leading-none text-slate-900">{value}</div>
        {hint ? <div className="mt-1 text-[11.5px] text-slate-400">{hint}</div> : null}
    </div>
);

const Th = ({ children, className = '' }) => (
    <th className={`whitespace-nowrap px-3 py-2 text-left text-[11.5px] font-semibold uppercase tracking-wider text-slate-500 ${className}`}>
        {children}
    </th>
);

const Td = ({ children, className = '' }) => (
    <td className={`px-3 py-2 align-middle text-[13px] text-slate-700 ${className}`}>{children}</td>
);

export default function TouchesView({ apiBaseUrl, withAccessTokenHeader, showToast }) {
    const headers = useCallback(
        () => (withAccessTokenHeader ? withAccessTokenHeader() : {}),
        [withAccessTokenHeader],
    );
    const base = `${apiBaseUrl}/api/cdr`;

    /* showToast приходит новой функцией на каждый рендер родителя. В зависимостях
       эффекта это означало бы перезапрос данных при каждом чужом рендере, поэтому
       держим его в ref — тот же приём, что в остальных разделах. */
    const toastRef = useRef(showToast);
    toastRef.current = showToast;

    const [range, setRange] = useState(() => ({ from: dayShift(6), to: dayShift(0) }));
    const [callType, setCallType] = useState('');
    const [filters, setFilters] = useState(EMPTY_FILTERS);
    const [filtersOpen, setFiltersOpen] = useState(false);
    const [phoneInput, setPhoneInput] = useState('');
    const [phone, setPhone] = useState('');
    const [page, setPage] = useState(1);
    const [tab, setTab] = useState('touches');

    /* Смена любого фильтра возвращает на первую страницу — и делает это В ТОМ ЖЕ
       обновлении состояния, а не эффектом следом. Эффектом получалось два
       запроса: сперва со старой страницей (её результат успевал отрисоваться),
       потом с первой. */
    const applyRange = (next) => {
        setRange({ from: next.from || next.to, to: next.to || next.from });
        setPage(1);
    };
    const applyCallType = (value) => { setCallType(value); setPage(1); };
    const applyFilters = (updater) => { setFilters(updater); setPage(1); };
    const resetFilters = () => {
        setFilters(EMPTY_FILTERS);
        setCallType('');
        setPhoneInput('');
        setPhone('');
        setPage(1);
    };

    const [meta, setMeta] = useState(null);
    const [data, setData] = useState(null);
    const [stats, setStats] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);
    const [downloading, setDownloading] = useState(false);

    const poll = useRef(null);

    useEffect(() => {
        let alive = true;
        axios.get(`${base}/meta`, { headers: headers() })
            .then((response) => { if (alive) setMeta(response.data); })
            .catch(() => { /* раздел работает и без меты: она только подписи */ });
        return () => { alive = false; };
    }, [base, headers]);

    /* Задержка поиска по телефону. Зависимость только от значения поля: положи
       сюда нестабильный колбэк — таймер сбрасывался бы на каждом рендере и
       запрос не уходил бы вовсе. */
    useEffect(() => {
        const timer = setTimeout(() => {
            setPhone(phoneInput.replace(/\D/g, ''));
            setPage(1);
        }, SEARCH_DEBOUNCE_MS);
        return () => clearTimeout(timer);
    }, [phoneInput]);

    const query = useMemo(() => {
        const params = {
            date_from: range.from,
            date_to: range.to,
            page,
            page_size: PAGE_SIZE,
        };
        if (callType) params.call_type = callType;
        if (filters.result) params.result = filters.result;
        if (filters.ext) params.ext = filters.ext;
        if (filters.queue) params.queue = filters.queue;
        if (filters.talkedOnly) params.talked_only = 1;
        if (phone) params.phone = phone;
        return params;
    }, [range.from, range.to, page, callType, filters, phone]);

    const load = useCallback((withSync) => {
        setError(null);
        return axios.get(`${base}/period`, {
            headers: headers(),
            params: { ...query, sync: withSync ? 1 : 0 },
        })
            .then((response) => setData(response.data))
            .catch((exc) => setError(errText(exc, 'Не удалось загрузить касания')))
            .finally(() => setLoading(false));
    }, [base, headers, query]);

    /* Первый заход за период сам ставит недостающие сутки в очередь: человек
       пришёл за данными, а не за кнопкой «а теперь загрузите их». */
    useEffect(() => {
        setLoading(true);
        load(true);
    }, [load]);

    const pending = data?.coverage?.pending || 0;

    // Пока сутки выкачиваются — подтягиваем прогресс; закончилось — опрос
    // прекращается сам. Синхронизацию при этом не перезапускаем: очередь на
    // сервере уже стоит, и второй запрос только добавил бы ей работы.
    useEffect(() => {
        clearInterval(poll.current);
        if (pending > 0) poll.current = setInterval(() => load(false), POLL_MS);
        return () => clearInterval(poll.current);
    }, [pending, load]);

    // Разрезы по операторам и дням тянем отдельно и только когда их смотрят:
    // это два GROUP BY по всему периоду, и на каждом опросе прогресса они были
    // бы лишней работой базы.
    useEffect(() => {
        /* Ждать полного периода нельзя: сегодняшние сутки дочитываются весь день,
           и на этом вкладки не грузились бы вовсе. Показываем по тому, что есть. */
        if (tab === 'touches') return undefined;
        let alive = true;
        const { page: _p, page_size: _s, ...rest } = query;
        axios.get(`${base}/stats`, { headers: headers(), params: rest })
            .then((response) => { if (alive) setStats(response.data); })
            .catch(() => { if (alive) setStats(null); });
        return () => { alive = false; };
    }, [base, headers, query, tab]);



    const resync = () => {
        axios.post(`${base}/sync`, null, {
            headers: headers(),
            params: { date_from: range.from, date_to: range.to, force: 1 },
        })
            .then(() => load(false))
            .catch((exc) => toastRef.current?.(
                errText(exc, 'Не удалось обновить данные со станции'), 'error'));
    };

    const download = () => {
        setDownloading(true);
        const { page: _p, page_size: _s, ...rest } = query;
        axios.get(`${base}/export`, { headers: headers(), params: rest, responseType: 'blob' })
            .then((response) => {
                const url = URL.createObjectURL(response.data);
                const link = document.createElement('a');
                link.href = url;
                link.download = exportFileName(range.from, range.to);
                document.body.appendChild(link);
                link.click();
                link.remove();
                URL.revokeObjectURL(url);
            })
            .catch(async (exc) => {
                // Ошибку сервер прислал JSON-ом, а мы просили blob — разворачиваем.
                let message = 'Не удалось собрать выгрузку';
                try {
                    const text = await exc?.response?.data?.text?.();
                    message = JSON.parse(text || '{}').error || message;
                } catch (_) { /* пусто — останется общая фраза */ }
                toastRef.current?.(message, 'error');
            })
            .finally(() => setDownloading(false));
    };

    const summary = data?.summary || {};
    const coverage = data?.coverage || {};
    const touches = data?.touches || [];
    const total = data?.total || 0;
    const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));
    const activeFilters = [
        /* Тип звонка выбирается сегментами над таблицей, а не в панели фильтров,
           но фильтром быть от этого не перестаёт: без него счётчик «Фильтры · N»
           врал, а «сбросить» его не снимал. */
        callType && { key: 'callType', label: callType },
        filters.result && { key: 'result', label: filters.result },
        filters.ext && { key: 'ext', label: `Номер ${filters.ext}` },
        filters.queue && { key: 'queue', label: `Очередь ${filters.queue}` },
        filters.talkedOnly && { key: 'talkedOnly', label: 'Только разговоры' },
        phone && { key: 'phone', label: `Телефон …${phone.slice(-4)}` },
    ].filter(Boolean);

    const dropFilter = (key) => {
        if (key === 'phone') { setPhoneInput(''); setPhone(''); setPage(1); return; }
        if (key === 'callType') { applyCallType(''); return; }
        applyFilters((prev) => ({ ...prev, [key]: key === 'talkedOnly' ? false : '' }));
    };

    const resultOptions = useMemo(() => [
        { value: '', label: 'Любой результат' },
        ...(data?.filter_values?.results || []).map((value) => ({ value, label: value })),
    ], [data]);

    const queueOptions = useMemo(() => [
        { value: '', label: 'Любая очередь' },
        ...(data?.filter_values?.queues || []).map((value) => ({ value, label: value })),
    ], [data]);

    /* Состояние моста берём из ответа периода, а не из меты: мета грузится один
       раз при входе, а мост может отвалиться, пока человек смотрит на экран. */
    const bridge = coverage.bridge || meta?.bridge || null;
    const bridgeDown = bridge && !bridge.connected;

    return (
        <div className="mx-auto w-full max-w-[1240px] px-3 py-4 sm:px-5 sm:py-6"
             style={{ fontFamily: APPLE_FONT }}>
            <header className="flex flex-col gap-3 sm:flex-row sm:items-center">
                <div className="min-w-0 sm:flex-1">
                    <h1 className="text-[19px] font-semibold leading-tight text-slate-900">Касания</h1>
                    <p className="mt-0.5 text-[12.5px] text-slate-500">
                        Звонки отдела продаж из CDR АТС: кто звонил, чем закончилось,
                        сколько говорили и где запись.
                    </p>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                    <button type="button" className={iosBtnGhost} onClick={resync}
                            disabled={pending > 0}
                            title="Перечитать период со станции — если там дописали звонки задним числом">
                        <RefreshCw size={14} className={pending > 0 ? 'animate-spin' : ''} />
                    </button>
                    <button type="button" className={`${iosBtnPrimary} flex-1 sm:flex-none`}
                            onClick={download} disabled={downloading || !total}>
                        {downloading ? <Loader2 size={15} className="animate-spin" /> : <Download size={15} />}
                        {downloading ? 'Готовим…' : 'Выгрузить в Excel'}
                    </button>
                </div>
            </header>

            {bridgeDown ? (
                <div className="mt-4 flex items-start gap-2 rounded-2xl bg-amber-50 px-4 py-3 text-[13px] text-amber-800 ring-1 ring-amber-100">
                    <PlugZap size={16} className="mt-0.5 shrink-0" />
                    <span>
                        {/* Разделяем «никогда не приходил» и «замолчал»: это разные
                            поломки, и чинят их разные люди. */}
                        {bridge.last_seen_at ? (
                            <>
                                Мост в корпоративной сети молчит {silence(bridge.silent_minutes)}.
                                Новые сутки не приедут — показываем то, что уже в базе.
                                {bridge.hostname ? ` Последний раз выходил на связь с ${bridge.hostname}.` : ''}
                            </>
                        ) : (
                            <>
                                Мост ещё ни разу не выходил на связь. Станция стоит в
                                корпоративной сети, и данные приносит служба оттуда —
                                пока её не подняли, раздел показывает пустой период.
                            </>
                        )}
                        {bridge.last_error ? (
                            <span className="mt-1 block text-[12.5px] text-amber-700">
                                Последняя ошибка: {bridge.last_error}
                            </span>
                        ) : null}
                    </span>
                </div>
            ) : null}

            {/* Период и поиск — одной полосой: с них начинается работа с разделом. */}
            <section className="mt-4 flex flex-col gap-2.5 sm:flex-row sm:items-center">
                <IosDateRangePicker
                    from={range.from} to={range.to}
                    max={meta?.today || isoDate(new Date())}
                    presets={DATE_PRESETS}
                    onChange={applyRange}
                />
                <div className="relative sm:flex-1">
                    <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                    <input
                        type="search" className={`${iosInput} pl-9`} value={phoneInput}
                        onChange={(event) => setPhoneInput(event.target.value)}
                        placeholder="Телефон клиента — хватит последних цифр" />
                </div>
                <button type="button" onClick={() => setFiltersOpen((open) => !open)}
                        className={`${activeFilters.length ? iosBtnPrimary : iosBtnSecondary} shrink-0`}>
                    <Filter size={14} />
                    Фильтры{activeFilters.length ? ` · ${activeFilters.length}` : ''}
                </button>
            </section>

            {filtersOpen ? (
                <section className={`${iosCard} mt-3 grid gap-3 p-3.5 sm:grid-cols-2 lg:grid-cols-4`}>
                    <label className="block space-y-1.5">
                        <span className={iosGroupLabel}>Результат</span>
                        <CustomSelect variant="ios" value={filters.result} options={resultOptions}
                                      onChange={(value) => applyFilters((prev) => ({ ...prev, result: value }))} />
                    </label>
                    <label className="block space-y-1.5">
                        <span className={iosGroupLabel}>Внутренний номер</span>
                        <input className={iosInput} value={filters.ext} inputMode="numeric"
                               placeholder="например 6474"
                               onChange={(event) => applyFilters((prev) => ({
                                   ...prev, ext: event.target.value.replace(/\D/g, '').slice(0, 6),
                               }))} />
                    </label>
                    <label className="block space-y-1.5">
                        <span className={iosGroupLabel}>Очередь</span>
                        <CustomSelect variant="ios" value={filters.queue} options={queueOptions}
                                      onChange={(value) => applyFilters((prev) => ({ ...prev, queue: value }))} />
                    </label>
                    <label className="flex items-end gap-2 pb-1">
                        <input type="checkbox" checked={filters.talkedOnly}
                               className="h-4 w-4 rounded border-slate-300 text-blue-600 focus:ring-blue-500/70"
                               onChange={(event) => applyFilters((prev) => ({
                                   ...prev, talkedOnly: event.target.checked,
                               }))} />
                        <span className="text-[13px] text-slate-700">Только состоявшиеся разговоры</span>
                    </label>
                </section>
            ) : null}

            {activeFilters.length ? (
                <div className="mt-2.5 flex flex-wrap items-center gap-1.5">
                    {activeFilters.map((chip) => (
                        <button key={chip.key} type="button" onClick={() => dropFilter(chip.key)}
                                className="inline-flex items-center gap-1 rounded-full bg-white px-2.5 py-1 text-[12.5px] text-slate-700 ring-1 ring-slate-200/80 transition hover:bg-slate-50">
                            {chip.label}
                            <X size={12} className="text-slate-400" />
                        </button>
                    ))}
                    <button type="button" onClick={resetFilters}
                            className="px-1.5 text-[12.5px] font-medium text-slate-500 hover:text-slate-800">
                        сбросить
                    </button>
                </div>
            ) : null}

            {pending > 0 && !bridgeDown ? (
                <SyncProgress coverage={coverage} range={range} />
            ) : null}

            {coverage.errors?.length ? (
                <div className="mt-3 rounded-2xl bg-rose-50 px-4 py-3 text-[13px] text-rose-700 ring-1 ring-rose-100">
                    Станция не отдала {coverage.errors.length}&nbsp;сут.:{' '}
                    {coverage.errors.slice(0, 3).map((row) => shortDay(row.day)).join(', ')}
                    {coverage.errors.length > 3 ? '…' : ''}. Нажмите «обновить» — попробуем ещё раз.
                </div>
            ) : null}

            {error ? (
                <div className={`${iosCard} mt-3 flex items-center gap-2 px-4 py-3 text-[13px] text-amber-700`}>
                    {error}
                    <button type="button" className="font-semibold text-amber-800 underline"
                            onClick={() => load(false)}>Повторить</button>
                </div>
            ) : null}

            {data ? (
            <section className="mt-4 grid grid-cols-2 gap-2.5 sm:grid-cols-3 lg:grid-cols-6">
                <Metric label="Касаний" value={(summary.total || 0).toLocaleString('ru-RU')} />
                <Metric label="Разговоров" value={(summary.talks || 0).toLocaleString('ru-RU')}
                        hint={`${percent(summary.talks || 0, summary.total || 0)}% дозваниваемость`} />
                <Metric label="Время разговоров" value={`${hours(summary.talk_seconds)} ч`} />
                <Metric label="Исходящих" value={(summary.outgoing || 0).toLocaleString('ru-RU')} />
                <Metric label="Входящих" value={(summary.incoming || 0).toLocaleString('ru-RU')}
                        hint={`не приняли ${summary.incoming_missed || 0}`} />
                <Metric label="Клиентов" value={(summary.phones || 0).toLocaleString('ru-RU')}
                        hint={`${summary.operators || 0} внутр. номеров`} />
            </section>
            ) : null}

            <div className="mt-4 flex flex-col gap-2.5 sm:flex-row sm:items-center sm:justify-between">
                <IosSegmented value={tab} options={TABS} onChange={setTab} ariaLabel="Что показывать" />
                {tab === 'touches' ? (
                    <IosSegmented value={callType} options={TYPE_SEGMENTS} onChange={applyCallType}
                                  ariaLabel="Тип звонка" />
                ) : null}
            </div>

            {loading && !data ? (
                <div className={`${iosCard} mt-3 flex items-center justify-center gap-2 px-4 py-10 text-[13px] text-slate-400`}>
                    <Loader2 size={15} className="animate-spin" /> считаем…
                </div>
            ) : null}

            {tab === 'touches' && data ? (
                <TouchesTable touches={touches} total={total} page={page} pageCount={pageCount}
                              onPage={setPage} />
            ) : null}
            {tab === 'operators' && data ? <OperatorsTable rows={stats?.operators || []} /> : null}
            {tab === 'daily' && data ? <DailyTable rows={stats?.daily || []} /> : null}

            <p className="mt-4 px-1 text-[11.5px] leading-relaxed text-slate-400">
                Касание — это один вызов, а не строка CDR: плечи склеены по linkedid.
                «Разговор» считается по плечу самого агента, без ожидания в очереди.
                Ссылки на записи открываются только из внутренней сети.
                {meta?.cached_from ? ` В базе уже есть данные с ${shortDay(meta.cached_from)}.` : ''}
            </p>
        </div>
    );
}

/* Прогресс выкачки. Показывает сутки, а не проценты «вообще»: сутки — это то,
   чем измеряется работа, и по ним видно, сколько ещё ждать. */
function SyncProgress({ coverage, range }) {
    const done = coverage.days_total - coverage.days_missing;
    return (
        <section className={`${iosCard} mt-3 px-4 py-3`}>
            <div className="flex items-center gap-2 text-[13px] text-slate-700">
                <Loader2 size={15} className="shrink-0 animate-spin text-blue-500" />
                <span className="min-w-0">
                    Мост забирает со станции {rangeLabel(range.from, range.to)}: сутки{' '}
                    <b className="tabular-nums">{Math.max(done, 0)}</b> из{' '}
                    <b className="tabular-nums">{coverage.days_total}</b>
                    {coverage.rows_fetched
                        ? ` · прочитано ${coverage.rows_fetched.toLocaleString('ru-RU')} строк CDR`
                        : ''}
                </span>
            </div>
            <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-slate-100">
                <div className="h-full rounded-full bg-blue-500 transition-all duration-500"
                     style={{ width: `${Math.max(4, coverage.percent || 0)}%` }} />
            </div>
            <p className="mt-1.5 text-[11.5px] text-slate-400">
                Страницу можно закрыть — сутки забирает служба в корпоративной сети,
                а забранное остаётся в базе: второй заход за этот период мгновенный.
            </p>
        </section>
    );
}

function TouchesTable({ touches, total, page, pageCount, onPage }) {
    if (!touches.length) {
        return (
            <div className={`${iosCard} mt-3 px-4 py-10 text-center text-[13px] text-slate-500`}>
                За этот период под фильтры ничего не попало.
            </div>
        );
    }
    return (
        <>
            <div className={`${iosCard} mt-3 hidden overflow-x-auto md:block`}>
                <table className="w-full min-w-[1020px] border-collapse">
                    <thead className="bg-slate-50">
                        <tr>
                            <Th>Когда</Th>
                            <Th>Клиент</Th>
                            <Th>Оператор</Th>
                            <Th>Тип</Th>
                            <Th>Результат</Th>
                            <Th className="text-right">Разговор</Th>
                            <Th className="text-right">Вызов</Th>
                            <Th>Очередь</Th>
                            <Th>Запись</Th>
                        </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100">
                        {touches.map((touch) => (
                            <tr key={`${touch.linkedid}-${touch.phone}`} className="hover:bg-slate-50/70">
                                <Td className="whitespace-nowrap tabular-nums">
                                    <span className="text-slate-500">{shortDay(touch.started_at)}</span>{' '}
                                    {shortTime(touch.started_at)}
                                    {touch.answered_at ? (
                                        <span className="ml-1 text-[11.5px] text-slate-400"
                                              title="Начало плеча, на котором состоялся разговор — момент, когда вызов дошёл до оператора">
                                            → {shortTime(touch.answered_at)}
                                        </span>
                                    ) : null}
                                </Td>
                                <Td className="whitespace-nowrap tabular-nums">{prettyPhone(touch.phone)}</Td>
                                <Td>
                                    <div className="truncate max-w-[220px]">{touch.operator || '—'}</div>
                                    <div className="text-[11.5px] text-slate-400">
                                        {touch.ext ? `вн. ${touch.ext}` : 'номер не определён'}
                                        {touch.direction ? ` · ${touch.direction}` : ''}
                                    </div>
                                </Td>
                                <Td className="whitespace-nowrap">
                                    <span className="inline-flex items-center gap-1.5 text-slate-600">
                                        {touch.call_type === 'Исходящий' ? <PhoneOutgoing size={13} className="text-slate-400" />
                                            : touch.call_type === 'Входящий' ? <PhoneIncoming size={13} className="text-slate-400" />
                                                : <PhoneMissed size={13} className="text-slate-400" />}
                                        {touch.call_type}
                                    </span>
                                </Td>
                                <Td>
                                    <span className={`inline-flex rounded-full px-2.5 py-1 text-[11.5px] font-medium ring-1 ${
                                        resultTone(touch.result)}`}>
                                        {touch.result}
                                    </span>
                                </Td>
                                <Td className="whitespace-nowrap text-right tabular-nums">{hms(touch.talk_seconds)}</Td>
                                {/* Вся длительность вызова (duration из CDR), включая
                                    разговор — не «сколько звонили до ответа». Подпись
                                    честная, значение то же, что в прежних выгрузках. */}
                                <Td className="whitespace-nowrap text-right tabular-nums text-slate-500"
                                    title="Вся длительность вызова, включая разговор">
                                    {touch.dial_seconds ? hms(touch.dial_seconds) : '—'}
                                </Td>
                                <Td className="whitespace-nowrap tabular-nums text-slate-500">{touch.queue || '—'}</Td>
                                <Td>
                                    {touch.recording_url ? (
                                        /* Ссылка внутрисетевая, поэтому открывается новой вкладкой и
                                           честно подписана: снаружи она не откроется. */
                                        <a href={touch.recording_url} target="_blank" rel="noreferrer"
                                           title="Откроется только из внутренней сети"
                                           className="inline-flex items-center gap-1 text-[12.5px] font-medium text-blue-600 hover:underline">
                                            <Phone size={12} /> запись
                                        </a>
                                    ) : <span className="text-slate-300">—</span>}
                                </Td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>

            {/* На телефоне таблица в девять колонок нечитаема — карточки. */}
            <div className="mt-3 space-y-2 md:hidden">
                {touches.map((touch) => (
                    <div key={`${touch.linkedid}-${touch.phone}`} className={`${iosCard} px-3.5 py-3`}>
                        <div className="flex items-baseline justify-between gap-2">
                            <span className="text-[14px] font-medium tabular-nums text-slate-900">
                                {prettyPhone(touch.phone)}
                            </span>
                            <span className="shrink-0 text-[12px] tabular-nums text-slate-400">
                                {shortDay(touch.started_at)} {shortTime(touch.started_at)}
                            </span>
                        </div>
                        <div className="mt-1 text-[12.5px] text-slate-500">
                            {touch.operator || '—'}{touch.ext ? ` · вн. ${touch.ext}` : ''}
                        </div>
                        <div className="mt-2 flex flex-wrap items-center gap-1.5">
                            <span className={`inline-flex rounded-full px-2.5 py-1 text-[11.5px] font-medium ring-1 ${
                                resultTone(touch.result)}`}>
                                {touch.result}
                            </span>
                            <span className="text-[12px] text-slate-500">{touch.call_type}</span>
                            {touch.talk_seconds > 0 ? (
                                <span className="text-[12px] tabular-nums text-slate-500">· {hms(touch.talk_seconds)}</span>
                            ) : null}
                            {touch.recording_url ? (
                                <a href={touch.recording_url} target="_blank" rel="noreferrer"
                                   className="ml-auto text-[12.5px] font-medium text-blue-600">запись</a>
                            ) : null}
                        </div>
                    </div>
                ))}
            </div>

            <div className="mt-3">
                <IosPager page={page} pageCount={pageCount} total={total}
                          from={(page - 1) * PAGE_SIZE + 1}
                          to={Math.min(page * PAGE_SIZE, total)}
                          onPage={onPage} unit="касания" />
            </div>
        </>
    );
}

function OperatorsTable({ rows }) {
    if (!rows.length) {
        return (
            <div className={`${iosCard} mt-3 px-4 py-10 text-center text-[13px] text-slate-500`}>
                Пока считаем — или за период звонков не было.
            </div>
        );
    }
    return (
        <div className={`${iosCard} mt-3 overflow-x-auto`}>
            <table className="w-full min-w-[720px] border-collapse">
                <thead className="bg-slate-50">
                    <tr>
                        <Th>Оператор</Th>
                        <Th>Вн. номера</Th>
                        <Th className="text-right">Касаний</Th>
                        <Th className="text-right">Разговоров</Th>
                        <Th className="text-right">Дозваниваемость</Th>
                        <Th className="text-right">Разговор, ч</Th>
                    </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                    {rows.map((row) => (
                        <tr key={row.operator} className="hover:bg-slate-50/70">
                            <Td>
                                <div className="truncate max-w-[280px]">{row.operator}</div>
                                {row.direction ? (
                                    <div className="text-[11.5px] text-slate-400">{row.direction}</div>
                                ) : null}
                            </Td>
                            <Td className="tabular-nums text-slate-500">{row.exts || '—'}</Td>
                            <Td className="text-right tabular-nums">{row.touches.toLocaleString('ru-RU')}</Td>
                            <Td className="text-right tabular-nums">{row.talks.toLocaleString('ru-RU')}</Td>
                            <Td className="text-right tabular-nums">{percent(row.talks, row.touches)}%</Td>
                            <Td className="text-right tabular-nums">{hours(row.talk_seconds)}</Td>
                        </tr>
                    ))}
                </tbody>
            </table>
        </div>
    );
}

function DailyTable({ rows }) {
    const peak = rows.reduce((max, row) => Math.max(max, row.touches), 0);
    if (!rows.length) {
        return (
            <div className={`${iosCard} mt-3 px-4 py-10 text-center text-[13px] text-slate-500`}>
                Пока считаем — или за период звонков не было.
            </div>
        );
    }
    return (
        <div className={`${iosCard} mt-3 overflow-x-auto`}>
            <table className="w-full min-w-[620px] border-collapse">
                <thead className="bg-slate-50">
                    <tr>
                        <Th>День</Th>
                        <Th className="text-right">Касаний</Th>
                        <Th className="text-right">Разговоров</Th>
                        <Th className="text-right">Дозваниваемость</Th>
                        <Th className="text-right">Разговор, ч</Th>
                        <Th className="w-[30%]">Нагрузка</Th>
                    </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                    {rows.map((row) => (
                        <tr key={row.day} className="hover:bg-slate-50/70">
                            <Td className="whitespace-nowrap tabular-nums">{shortDay(row.day)}</Td>
                            <Td className="text-right tabular-nums">{row.touches.toLocaleString('ru-RU')}</Td>
                            <Td className="text-right tabular-nums">{row.talks.toLocaleString('ru-RU')}</Td>
                            <Td className="text-right tabular-nums">{percent(row.talks, row.touches)}%</Td>
                            <Td className="text-right tabular-nums">{hours(row.talk_seconds)}</Td>
                            <Td>
                                <div className="h-1.5 overflow-hidden rounded-full bg-slate-100">
                                    <div className="h-full rounded-full bg-blue-500"
                                         style={{ width: `${peak ? Math.round((100 * row.touches) / peak) : 0}%` }} />
                                </div>
                            </Td>
                        </tr>
                    ))}
                </tbody>
            </table>
        </div>
    );
}
