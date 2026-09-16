import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import {
    ChevronDown, ChevronRight, Download, FileJson, Filter, Loader2, Phone, PhoneIncoming,
    PhoneMissed, PhoneOutgoing, PlugZap, RefreshCw, Search, X,
} from 'lucide-react';

import {
    iosBtnGhost, iosBtnPrimary, iosBtnSecondary, iosCard, iosGroupLabel, iosInput, IosPager,
    IosSegmented, IosToggle,
} from '../ui/ios';
import CustomSelect from '../ui/CustomSelect';
import { IosDateRangePicker, isoDate, rangeLabel } from '../ui/DateRangePicker';
import { hms, percent, prettyPhone, resultTone, shortDay, shortTime, silence } from './touchMeta';
import {
    PRESENCE_OPTIONS, SOURCE_OPTIONS, countActiveFilters, dealsFileName, dealsQuery,
    reactionLabel, subjectOf,
} from './dealsMeta';

/* Режим «Сделки» раздела «Касания»: записи источника (сделки amoCRM, лиды «Потока»,
 * водители «Платного найма») и все звонки отдела по каждой из них.
 *
 * Вопрос руководителя здесь — не «сколько было звонков», а «позвонили ли по этой
 * заявке, с какой попытки дозвонились и кто». Поэтому строка — запись, а звонки
 * раскрываются под ней. Файл повторяет форму, к которой отдел привык за лето:
 * «Лиды» с блоками «Касание 1..N», «Касания» по звонку в строке, «Контекст» первым.
 *
 * Записи берутся из снимка «Воронки ОП» (ночная выгрузка в 05:20 за вчера), звонки —
 * из касаний, которые приносит мост. Оба источника могут отставать, и экран говорит
 * об этом словами, а не пустой таблицей: «за такие-то дни записей ещё нет», «мост
 * забирает сутки N из M».
 */

const POLL_MS = 3000;
const RUN_POLL_MS = 5000;
const SEARCH_DEBOUNCE_MS = 350;
const PAGE_SIZE = 50;

const errText = (error, fallback) => (
    error?.response?.data?.error || error?.message || fallback);

const EMPTY_FILTERS = {
    park: '', city: '', stage: '', leadType: '', owner: '', phone: '', presence: 'all',
    talkedOnly: false, ownOnly: false, noWindow: false, touchesTo: '',
};

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

const Td = ({ children, className = '', ...rest }) => (
    <td {...rest} className={`px-3 py-2 align-middle text-[13px] text-slate-700 ${className}`}>{children}</td>
);

const TypeIcon = ({ type }) => (
    type === 'Исходящий' ? <PhoneOutgoing size={13} className="text-slate-400" />
        : type === 'Входящий' ? <PhoneIncoming size={13} className="text-slate-400" />
            : <PhoneMissed size={13} className="text-slate-400" />);

const toOptions = (values, any) => [
    { value: '', label: any },
    ...(values || []).map((value) => (typeof value === 'string'
        ? { value, label: value }
        : { value: value.key, label: value.count ? `${value.label} · ${value.count}` : value.label })),
];

export default function DealsView({ apiBaseUrl, withAccessTokenHeader, showToast, presets, today }) {
    const headers = useCallback(
        () => (withAccessTokenHeader ? withAccessTokenHeader() : {}),
        [withAccessTokenHeader],
    );
    const base = `${apiBaseUrl}/api/cdr`;
    const toastRef = useRef(showToast);
    toastRef.current = showToast;

    const [source, setSource] = useState('amo');
    const [range, setRange] = useState(() => ({
        from: isoDate(new Date(Date.now() - 6 * 864e5)), to: isoDate(new Date()),
    }));
    const [filters, setFilters] = useState(EMPTY_FILTERS);
    const [filtersOpen, setFiltersOpen] = useState(false);
    const [phoneInput, setPhoneInput] = useState('');
    const [page, setPage] = useState(1);
    const [expanded, setExpanded] = useState(() => new Set());

    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);
    const [downloading, setDownloading] = useState(null);
    const [syncing, setSyncing] = useState(false);

    /* Любая смена условий возвращает на первую страницу тем же обновлением
       состояния — эффектом следом получалось два запроса подряд. */
    const applySource = (value) => { setSource(value); setFilters(EMPTY_FILTERS); setPhoneInput(''); setPage(1); };
    const applyRange = (next) => { setRange({ from: next.from || next.to, to: next.to || next.from }); setPage(1); };
    const applyFilters = (updater) => { setFilters(updater); setPage(1); };
    const resetFilters = () => { setFilters(EMPTY_FILTERS); setPhoneInput(''); setPage(1); };

    useEffect(() => {
        const timer = setTimeout(() => {
            const digits = phoneInput.replace(/\D/g, '');
            setFilters((prev) => (prev.phone === digits ? prev : { ...prev, phone: digits }));
            setPage(1);
        }, SEARCH_DEBOUNCE_MS);
        return () => clearTimeout(timer);
    }, [phoneInput]);

    const query = useMemo(
        () => dealsQuery(source, range, filters, { page, page_size: PAGE_SIZE }),
        [source, range.from, range.to, filters, page],
    );

    const load = useCallback((withSync) => {
        setError(null);
        return axios.get(`${base}/leads`, { headers: headers(), params: { ...query, sync: withSync ? 1 : 0 } })
            .then((response) => setData(response.data))
            .catch((exc) => setError(errText(exc, `Не удалось загрузить ${subjectOf(source).acc}`)))
            .finally(() => setLoading(false));
    }, [base, headers, query]);

    useEffect(() => { setLoading(true); setExpanded(new Set()); load(true); }, [load]);

    const pending = data?.coverage?.pending || 0;
    const lastRun = data?.leads_coverage?.last_run || null;
    const runActive = syncing || lastRun?.status === 'running';

    // Пока мост забирает сутки звонков или идёт догрузка записей — опрашиваем.
    useEffect(() => {
        if (!pending && !runActive) return undefined;
        const timer = setInterval(() => load(false), pending ? POLL_MS : RUN_POLL_MS);
        return () => clearInterval(timer);
    }, [pending, runActive, load]);

    useEffect(() => {
        if (syncing && lastRun && lastRun.status !== 'running') setSyncing(false);
    }, [syncing, lastRun]);

    const download = (format) => {
        setDownloading(format);
        const { page: _p, page_size: _s, ...rest } = query;
        axios.get(`${base}/leads/export`, {
            headers: headers(), params: { ...rest, format }, responseType: 'blob',
        })
            .then((response) => {
                const url = URL.createObjectURL(response.data);
                const link = document.createElement('a');
                link.href = url;
                link.download = dealsFileName(source, range.from, range.to, format);
                document.body.appendChild(link);
                link.click();
                link.remove();
                URL.revokeObjectURL(url);
            })
            .catch(async (exc) => {
                let message = 'Не удалось собрать выгрузку';
                try {
                    const text = await exc?.response?.data?.text?.();
                    message = JSON.parse(text || '{}').error || message;
                } catch (_) { /* останется общая фраза */ }
                toastRef.current?.(message, 'error');
            })
            .finally(() => setDownloading(null));
    };

    const syncLeads = () => {
        setSyncing(true);
        axios.post(`${base}/leads/sync`, null, {
            headers: headers(), params: { source, date_from: range.from, date_to: range.to },
        })
            .then(() => toastRef.current?.(`Догрузка ${subjectOf(source).many} из источника запущена — цифры обновятся сами`, 'success'))
            .catch((exc) => {
                setSyncing(false);
                toastRef.current?.(errText(exc, 'Не удалось запустить догрузку'), 'error');
            });
    };

    const summary = data?.summary || {};
    const coverage = data?.coverage || {};
    const leadsCoverage = data?.leads_coverage || {};
    const rows = data?.leads || [];
    const total = data?.total || 0;
    const values = data?.filter_values || {};
    const subject = subjectOf(source);
    const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));
    const activeCount = countActiveFilters(filters);
    const bridge = coverage.bridge || null;
    const bridgeDown = bridge && !bridge.connected;
    const missingDays = leadsCoverage.missing_days || [];
    const withoutPhone = leadsCoverage.without_phone || 0;

    const ownerLabel = (key) => values.owners?.find((o) => o.key === key)?.label || key;
    const chips = [
        filters.presence !== 'all' && { key: 'presence', label: PRESENCE_OPTIONS.find((o) => o.value === filters.presence)?.label },
        filters.owner && { key: 'owner', label: ownerLabel(filters.owner) },
        filters.park && { key: 'park', label: filters.park },
        filters.city && { key: 'city', label: filters.city },
        filters.stage && { key: 'stage', label: filters.stage },
        filters.leadType && { key: 'leadType', label: `Тип ${filters.leadType}` },
        filters.talkedOnly && { key: 'talkedOnly', label: 'Только разговоры' },
        filters.ownOnly && { key: 'ownOnly', label: 'Звонки ответственного' },
        filters.noWindow && { key: 'noWindow', label: 'Без окна по дате' },
        filters.touchesTo && { key: 'touchesTo', label: `Звонки до ${shortDay(filters.touchesTo)}` },
        filters.phone && { key: 'phone', label: `Телефон …${filters.phone.slice(-4)}` },
    ].filter(Boolean);

    const dropChip = (key) => {
        if (key === 'phone') { setPhoneInput(''); return; }
        applyFilters((prev) => ({ ...prev, [key]: key === 'presence' ? 'all' : (typeof EMPTY_FILTERS[key] === 'boolean' ? false : '') }));
    };

    const toggleRow = (key) => setExpanded((prev) => {
        const next = new Set(prev);
        if (next.has(key)) next.delete(key); else next.add(key);
        return next;
    });

    return (
        <>
            {/* Источник, период, поиск и выгрузка — одной полосой: с них начинается работа. */}
            <section className="mt-4 flex flex-col gap-2.5 lg:flex-row lg:items-center">
                <IosSegmented value={source} options={SOURCE_OPTIONS} onChange={applySource} ariaLabel="Источник записей" />
                <IosDateRangePicker from={range.from} to={range.to} max={today || isoDate(new Date())}
                                    presets={presets} onChange={applyRange} />
                <div className="relative lg:flex-1">
                    <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                    <input type="search" className={`${iosInput} pl-9`} value={phoneInput}
                           onChange={(event) => setPhoneInput(event.target.value)}
                           placeholder="Телефон — хватит последних цифр" />
                </div>
                <div className="flex items-center gap-2">
                    <button type="button" onClick={() => setFiltersOpen((open) => !open)}
                            className={`${activeCount ? iosBtnPrimary : iosBtnSecondary} shrink-0`}>
                        <Filter size={14} />
                        Фильтры{activeCount ? ` · ${activeCount}` : ''}
                    </button>
                    <button type="button" className={`${iosBtnGhost} shrink-0`} onClick={() => download('json')}
                            disabled={!!downloading || !total} title="Тот же файл в JSON — для ИИ и скриптов">
                        {downloading === 'json' ? <Loader2 size={15} className="animate-spin" /> : <FileJson size={15} />}
                    </button>
                    <button type="button" className={`${iosBtnPrimary} shrink-0`} onClick={() => download('xlsx')}
                            disabled={!!downloading || !total}>
                        {downloading === 'xlsx' ? <Loader2 size={15} className="animate-spin" /> : <Download size={15} />}
                        {downloading === 'xlsx' ? 'Готовим…' : 'Выгрузить в Excel'}
                    </button>
                </div>
            </section>

            {filtersOpen ? (
                <section className={`${iosCard} mt-3 grid gap-3 p-3.5 sm:grid-cols-2 lg:grid-cols-4`}>
                    <label className="block space-y-1.5">
                        <span className={iosGroupLabel}>Ответственный</span>
                        <CustomSelect variant="ios" value={filters.owner} options={toOptions(values.owners, 'Любой')}
                                      onChange={(value) => applyFilters((prev) => ({ ...prev, owner: value }))} />
                    </label>
                    <label className="block space-y-1.5">
                        <span className={iosGroupLabel}>Парк</span>
                        <CustomSelect variant="ios" value={filters.park} options={toOptions(values.parks, 'Любой парк')}
                                      onChange={(value) => applyFilters((prev) => ({ ...prev, park: value }))} />
                    </label>
                    <label className="block space-y-1.5">
                        <span className={iosGroupLabel}>Город</span>
                        <CustomSelect variant="ios" value={filters.city} options={toOptions(values.cities, 'Любой город')}
                                      onChange={(value) => applyFilters((prev) => ({ ...prev, city: value }))} />
                    </label>
                    <label className="block space-y-1.5">
                        <span className={iosGroupLabel}>Статус</span>
                        <CustomSelect variant="ios" value={filters.stage} options={toOptions(values.stages, 'Любой статус')}
                                      onChange={(value) => applyFilters((prev) => ({ ...prev, stage: value }))} />
                    </label>
                    {values.lead_types?.length ? (
                        <label className="block space-y-1.5">
                            <span className={iosGroupLabel}>Тип лида</span>
                            <CustomSelect variant="ios" value={filters.leadType} options={toOptions(values.lead_types, 'Любой тип')}
                                          onChange={(value) => applyFilters((prev) => ({ ...prev, leadType: value }))} />
                        </label>
                    ) : null}
                    <label className="block space-y-1.5">
                        <span className={iosGroupLabel}>Звонки</span>
                        <IosSegmented value={filters.presence} options={PRESENCE_OPTIONS} stretch
                                      onChange={(value) => applyFilters((prev) => ({ ...prev, presence: value }))}
                                      ariaLabel="Наличие звонков" />
                    </label>
                    <label className="block space-y-1.5" title="По умолчанию звонки берутся на сутки дольше периода записей — ради дожима последних заявок">
                        <span className={iosGroupLabel}>Звонки по дату</span>
                        <input type="date" className={iosInput} value={filters.touchesTo}
                               min={range.to} max={today || isoDate(new Date())}
                               onChange={(event) => applyFilters((prev) => ({ ...prev, touchesTo: event.target.value }))} />
                    </label>
                    <div className="space-y-2 pt-1">
                        <label className="flex items-center justify-between gap-3 text-[13px] text-slate-700"
                               title="Оставить только касания, которые закончились разговором">
                            Только разговоры
                            <IosToggle checked={filters.talkedOnly}
                                       onChange={(checked) => applyFilters((prev) => ({ ...prev, talkedOnly: checked }))} />
                        </label>
                        <label className="flex items-center justify-between gap-3 text-[13px] text-slate-700"
                               title="Звонки коллег по тем же записям убираются; «0 касаний» тогда значит «ответственный не звонил»">
                            Только звонки ответственного
                            <IosToggle checked={filters.ownOnly}
                                       onChange={(checked) => applyFilters((prev) => ({ ...prev, ownOnly: checked }))} />
                        </label>
                        <label className="flex items-center justify-between gap-3 text-[13px] text-slate-700"
                               title="Для записей с уникальным номером взять все звонки по номеру, а не только внутри окна записи">
                            Все звонки по номеру
                            <IosToggle checked={filters.noWindow}
                                       onChange={(checked) => applyFilters((prev) => ({ ...prev, noWindow: checked }))} />
                        </label>
                    </div>
                </section>
            ) : null}

            {chips.length ? (
                <div className="mt-2.5 flex flex-wrap items-center gap-1.5">
                    {chips.map((chip) => (
                        <button key={chip.key} type="button" onClick={() => dropChip(chip.key)}
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

            {bridgeDown ? (
                <div className="mt-3 flex items-start gap-2 rounded-2xl bg-amber-50 px-4 py-3 text-[13px] text-amber-800 ring-1 ring-amber-100">
                    <PlugZap size={16} className="mt-0.5 shrink-0" />
                    <span>
                        Мост в корпоративной сети молчит{bridge.silent_minutes != null ? ` ${silence(bridge.silent_minutes)}` : ''}.
                        Новые звонки не приедут — показываем то, что уже в базе.
                    </span>
                </div>
            ) : null}

            {pending > 0 && !bridgeDown ? (
                <section className={`${iosCard} mt-3 px-4 py-3`}>
                    <div className="flex items-center gap-2 text-[13px] text-slate-700">
                        <Loader2 size={15} className="shrink-0 animate-spin text-blue-500" />
                        <span>
                            Мост забирает звонки за {rangeLabel(range.from, data?.period?.touches_to || range.to)}: сутки{' '}
                            <b className="tabular-nums">{Math.max(coverage.days_total - coverage.days_missing, 0)}</b> из{' '}
                            <b className="tabular-nums">{coverage.days_total}</b>
                        </span>
                    </div>
                    <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-slate-100">
                        <div className="h-full rounded-full bg-blue-500 transition-all duration-500"
                             style={{ width: `${Math.max(4, coverage.percent || 0)}%` }} />
                    </div>
                </section>
            ) : null}

            {missingDays.length || withoutPhone || runActive || lastRun?.status === 'error' ? (
                <div className={`${iosCard} mt-3 flex flex-col gap-2 px-4 py-3 text-[13px] text-slate-700 sm:flex-row sm:items-center`}>
                    <span className="min-w-0 sm:flex-1">
                        {runActive ? (
                            <span className="inline-flex items-center gap-2">
                                <Loader2 size={14} className="animate-spin text-blue-500" />
                                Догружаем {subject.acc} из источника — таблица обновится сама.
                            </span>
                        ) : (
                            <>
                                {withoutPhone ? (
                                    /* Снимок «Воронки ОП» хранит телефоны с 16.09.2026; карточки,
                                       снятые раньше, лежат без номера — по ним звонки не найти,
                                       пока не дочитаны контакты. */
                                    <>У {withoutPhone.toLocaleString('ru-RU')} {subject.many} в снимке нет телефона —
                                    звонки по ним не найти, пока не дочитаны контакты из источника. </>
                                ) : null}
                                {missingDays.length ? (
                                    <>За {missingDays.length === 1 ? 'сутки' : `${missingDays.length} сут.`}{' '}
                                    ({missingDays.slice(0, 4).map(shortDay).join(', ')}{missingDays.length > 4 ? '…' : ''}) {subject.many}
                                    в снимке нет: либо их не было, либо ночная выгрузка ещё не прошла. </>
                                ) : null}
                                {!withoutPhone && !missingDays.length && lastRun?.status === 'error' ? (
                                    <span className="text-rose-700">Последняя догрузка не удалась: {lastRun.error}</span>
                                ) : null}
                            </>
                        )}
                    </span>
                    {!runActive ? (
                        <button type="button" className={`${iosBtnSecondary} shrink-0`} onClick={syncLeads}
                                title={`Дочитать ${subject.acc} за период из источника — сутки без снимка, незакрытые и карточки без телефона. Аудио не скачивается: у звонков остаются ссылки`}>
                            <RefreshCw size={14} /> Догрузить {subject.acc}
                        </button>
                    ) : null}
                </div>
            ) : null}

            {error ? (
                <div className={`${iosCard} mt-3 flex items-center gap-2 px-4 py-3 text-[13px] text-amber-700`}>
                    {error}
                    <button type="button" className="font-semibold text-amber-800 underline" onClick={() => load(false)}>Повторить</button>
                </div>
            ) : null}

            {data ? (
                <section className="mt-4 grid grid-cols-2 gap-2.5 sm:grid-cols-3 lg:grid-cols-6">
                    <Metric label={subject.many[0].toUpperCase() + subject.many.slice(1)} value={(summary.leads || 0).toLocaleString('ru-RU')} />
                    <Metric label="Со звонком" value={(summary.with_touches || 0).toLocaleString('ru-RU')}
                            hint={`${percent(summary.with_touches || 0, summary.leads || 0)}% · без звонка ${summary.without_touches || 0}`} />
                    <Metric label="Касаний" value={(summary.touches || 0).toLocaleString('ru-RU')}
                            hint={`исх. ${summary.outgoing || 0} · вх. ${summary.incoming || 0}`} />
                    <Metric label="Разговоров" value={(summary.talks || 0).toLocaleString('ru-RU')}
                            hint={`${percent(summary.talks || 0, summary.touches || 0)}% касаний`} />
                    <Metric label="Реакция ОП" value={reactionLabel(summary.median_reaction_min)} hint="медиана до 1-го исходящего" />
                    <Metric label="Ответственный звонил" value={(summary.owner_called?.yes || 0).toLocaleString('ru-RU')}
                            hint={`нет ${summary.owner_called?.no || 0} · пусто ${summary.owner_called?.blank || 0}`} />
                </section>
            ) : null}

            {loading && !data ? (
                <div className={`${iosCard} mt-3 flex items-center justify-center gap-2 px-4 py-10 text-[13px] text-slate-400`}>
                    <Loader2 size={15} className="animate-spin" /> считаем…
                </div>
            ) : null}

            {data && !rows.length ? (
                <div className={`${iosCard} mt-3 px-4 py-10 text-center text-[13px] text-slate-500`}>
                    За этот период под фильтры ничего не попало.
                </div>
            ) : null}

            {rows.length ? (
                <>
                    <div className={`${iosCard} mt-3 hidden overflow-x-auto md:block`}>
                        <table className="w-full min-w-[1040px] border-collapse">
                            <thead className="bg-slate-50">
                                <tr>
                                    <Th className="w-8" />
                                    <Th>{subject.one[0].toUpperCase() + subject.one.slice(1)}</Th>
                                    <Th>Телефон</Th>
                                    <Th>Ответственный</Th>
                                    <Th>Статус</Th>
                                    <Th className="text-right">Касаний</Th>
                                    <Th>Первый звонок</Th>
                                    <Th>Кто звонил</Th>
                                    <Th>Отв. звонил</Th>
                                </tr>
                            </thead>
                            <tbody className="divide-y divide-slate-100">
                                {rows.map((row) => {
                                    const open = expanded.has(row.key);
                                    return (
                                        <React.Fragment key={`${row.stream_type}-${row.key}`}>
                                            <tr className={`cursor-pointer hover:bg-slate-50/70 ${open ? 'bg-slate-50/60' : ''}`}
                                                onClick={() => toggleRow(row.key)}>
                                                <Td className="text-slate-400">
                                                    {row.touches?.length ? (open ? <ChevronDown size={14} /> : <ChevronRight size={14} />) : null}
                                                </Td>
                                                <Td>
                                                    <div className="max-w-[260px] truncate font-medium text-slate-900">{row.full_name || `#${row.key}`}</div>
                                                    <div className="text-[11.5px] tabular-nums text-slate-400">
                                                        {row.moment ? `${shortDay(row.moment)} ${shortTime(row.moment).slice(0, 5)}` : '—'}
                                                        {row.park ? ` · ${row.park}` : ''}{row.lead_type ? ` · ${row.lead_type}` : ''}
                                                        {row.dup ? ` · дубль ${row.dup}` : ''}
                                                    </div>
                                                </Td>
                                                <Td className="whitespace-nowrap tabular-nums">{prettyPhone(row.phone)}</Td>
                                                <Td><div className="max-w-[200px] truncate">{row.owner || '—'}</div></Td>
                                                <Td><div className="max-w-[240px] truncate" title={row.reason || ''}>{row.stage || '—'}</div></Td>
                                                <Td className="whitespace-nowrap text-right tabular-nums">
                                                    {row.agg?.touches ? (
                                                        <>
                                                            <span className="font-medium text-slate-900">{row.agg.touches}</span>
                                                            {row.agg.talks ? <span className="text-emerald-600"> · {row.agg.talks} разг.</span> : null}
                                                        </>
                                                    ) : <span className="text-slate-300">—</span>}
                                                </Td>
                                                <Td className="whitespace-nowrap tabular-nums text-slate-600">
                                                    {row.agg?.first_out_at ? (
                                                        <>
                                                            {shortDay(row.agg.first_out_at)} {shortTime(row.agg.first_out_at).slice(0, 5)}
                                                            <span className="ml-1 text-[11.5px] text-slate-400">через {reactionLabel(row.agg.reaction_min)}</span>
                                                        </>
                                                    ) : (row.agg?.first_at ? `${shortDay(row.agg.first_at)} ${shortTime(row.agg.first_at).slice(0, 5)} · вх.` : '—')}
                                                </Td>
                                                <Td><div className="max-w-[220px] truncate text-slate-600">{row.agg?.operators?.join(', ') || '—'}</div></Td>
                                                <Td>
                                                    {row.owner_called ? (
                                                        <span className={`inline-flex rounded-full px-2 py-0.5 text-[11.5px] font-medium ring-1 ${
                                                            row.owner_called === 'да' ? 'bg-emerald-50 text-emerald-700 ring-emerald-100' : 'bg-amber-50 text-amber-700 ring-amber-100'}`}>
                                                            {row.owner_called}
                                                        </span>
                                                    ) : <span className="text-slate-300">—</span>}
                                                </Td>
                                            </tr>
                                            {open && row.touches?.length ? (
                                                <tr className="bg-slate-50/40">
                                                    <td />
                                                    <td colSpan={8} className="px-3 pb-3 pt-1">
                                                        <TouchList touches={row.touches} moment={row.moment} />
                                                    </td>
                                                </tr>
                                            ) : null}
                                        </React.Fragment>
                                    );
                                })}
                            </tbody>
                        </table>
                    </div>

                    <div className="mt-3 space-y-2 md:hidden">
                        {rows.map((row) => {
                            const open = expanded.has(row.key);
                            return (
                                <div key={`${row.stream_type}-${row.key}`} className={`${iosCard} px-3.5 py-3`}
                                     onClick={() => toggleRow(row.key)}>
                                    <div className="flex items-baseline justify-between gap-2">
                                        <span className="min-w-0 truncate text-[14px] font-medium text-slate-900">{row.full_name || `#${row.key}`}</span>
                                        <span className="shrink-0 text-[12px] tabular-nums text-slate-400">{row.moment ? shortDay(row.moment) : ''}</span>
                                    </div>
                                    <div className="mt-1 text-[12.5px] tabular-nums text-slate-600">{prettyPhone(row.phone)}{row.owner ? ` · ${row.owner}` : ''}</div>
                                    <div className="mt-1 text-[12.5px] text-slate-500">{row.stage || '—'}</div>
                                    <div className="mt-2 flex flex-wrap items-center gap-1.5 text-[12px] text-slate-500">
                                        <span className="font-medium text-slate-900">{row.agg?.touches || 0} кас.</span>
                                        {row.agg?.talks ? <span className="text-emerald-600">· {row.agg.talks} разг.</span> : null}
                                        {row.agg?.reaction_min != null ? <span>· реакция {reactionLabel(row.agg.reaction_min)}</span> : null}
                                        {row.owner_called ? <span className="ml-auto">отв. звонил: {row.owner_called}</span> : null}
                                    </div>
                                    {open && row.touches?.length ? <div className="mt-2"><TouchList touches={row.touches} moment={row.moment} compact /></div> : null}
                                </div>
                            );
                        })}
                    </div>

                    <div className="mt-3">
                        <IosPager page={page} pageCount={pageCount} total={total}
                                  from={(page - 1) * PAGE_SIZE + 1} to={Math.min(page * PAGE_SIZE, total)}
                                  onPage={setPage} unit={subject.gen} />
                    </div>
                </>
            ) : null}

            <p className="mt-4 px-1 text-[11.5px] leading-relaxed text-slate-400">
                Звонок относится к карточке, если начался не раньше чем за 2 минуты до её появления и раньше
                следующей карточки с тем же номером — их часто заводят уже после начала разговора.
                Карточки берутся из ночной выгрузки «Воронки ОП», звонки приносит мост из корпоративной сети;
                записи разговоров не скачиваются — только ссылки, открываются из внутренней сети.
                {data?.period?.touches_to ? ` Звонки взяты по ${shortDay(data.period.touches_to)} включительно.` : ''}
            </p>
        </>
    );
}

/* Звонки одной записи под её строкой: время, кто, тип, результат, разговор, запись. */
function TouchList({ touches, moment, compact = false }) {
    return (
        <ol className={`divide-y divide-slate-100 rounded-xl bg-white ring-1 ring-slate-200/70 ${compact ? 'text-[12px]' : 'text-[12.5px]'}`}>
            {touches.map((touch, index) => {
                const before = moment && touch.started_at < moment;
                return (
                    <li key={`${touch.linkedid}-${touch.phone}`} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-1.5">
                        <span className="w-5 shrink-0 tabular-nums text-slate-400">{index + 1}</span>
                        <span className="tabular-nums text-slate-700" title={before ? 'Звонок начался до появления записи' : undefined}>
                            {shortDay(touch.started_at)} {shortTime(touch.started_at)}{before ? ' ↩' : ''}
                        </span>
                        <span className="inline-flex items-center gap-1 text-slate-600"><TypeIcon type={touch.call_type} />{touch.call_type}</span>
                        <span className="min-w-0 truncate text-slate-700">{touch.operator || '—'}{touch.ext ? <span className="text-slate-400"> · {touch.ext}</span> : null}</span>
                        <span className={`inline-flex rounded-full px-2 py-0.5 text-[11px] font-medium ring-1 ${resultTone(touch.result)}`}>{touch.result}</span>
                        <span className="tabular-nums text-slate-600">{hms(touch.talk_seconds)}</span>
                        {touch.recording_url ? (
                            <a href={touch.recording_url} target="_blank" rel="noreferrer" onClick={(event) => event.stopPropagation()}
                               className="ml-auto inline-flex items-center gap-1 font-medium text-blue-600 hover:underline"
                               title="Откроется только из внутренней сети">
                                <Phone size={11} /> запись
                            </a>
                        ) : null}
                    </li>
                );
            })}
        </ol>
    );
}
