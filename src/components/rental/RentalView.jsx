import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import { Car, CircleAlert, Download, Loader2, RefreshCw, Search, X } from 'lucide-react';
import IosDatePicker from '../ui/DatePicker';
import { APPLE_FONT, IosHint, IosMenu, IosSegmented, iosCard } from '../ui/ios';
import {
    FILTERS, GPS_LABELS, KIND_OPTIONS, RENT_LABELS, STATE_LABELS, STATES, clockTime, errorOf, exportFileName,
    failedSources, gpsSourceFailed, matchesQuery, pickLabel, shortTime, silenceLabel,
} from './rentalMeta';

/*
 * Раздел «Аренда авто» — ночной отчёт по арендным машинам (#398, ООЗ).
 *
 * Отчёт собирается сам в 01:00 и 07:00 из Yume и трёх GPS-систем; ночная смена только
 * проверяет офлайн и машины без GPS и ставит «Состояние» там, где его не подставил адрес.
 * Столбцы — ровно столбцы ТЗ; «Статус аренды» только в отчёте за 01:00 (ТЗ п. 3).
 *
 * Язык — тот же, что у «Кодов 2FA» и «Термокоробов»: белые поверхности с тонким кольцом,
 * кнопки строки инструментов 36 px. Цвет — только со смыслом: красный у офлайна, янтарный у
 * машин без GPS и у «Уточнить», остальное нейтральное.
 */

const TOOLBAR_BTN =
    'inline-flex h-9 shrink-0 items-center justify-center gap-1.5 rounded-xl bg-white px-3.5 text-[13px] font-medium text-slate-700 ring-1 ring-slate-200/70 shadow-[0_1px_2px_rgba(15,23,42,0.04)] transition hover:bg-slate-50 active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-50';

const TOOLBAR_BTN_PRIMARY =
    'inline-flex h-9 shrink-0 items-center justify-center gap-1.5 rounded-xl bg-blue-600 px-3.5 text-[13px] font-semibold text-white shadow-sm transition hover:bg-blue-700 active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-50';

const STATE_TRIGGER =
    'inline-flex h-8 max-w-full items-center gap-1 rounded-lg px-2 text-[13px] transition hover:bg-slate-100 disabled:opacity-50';

const HOW_IT_WORKS =
    'Отчёт собирается сам в 01:00 и 07:00: статус аренды берётся из Yume, сигнал и адрес — из Wialon, ГЛОНАСС и Efir. '
    + 'Машина в двух системах — одной строкой по самому свежему сигналу. Офлайн — больше 4 часов без трекинга. '
    + '«На базе» и «На ремонте» подставляются по адресу (Утеген Батыра 15А, 15 и Отарская — база; 15/1 и «Автомастерская, 17Б» — ремонт), '
    + 'остальное ставите вы. «Уточнить» — машина парка не в аренде и стоит не на базе.';

const Notice = ({ tone = 'amber', title, children, action }) => {
    const tones = {
        amber: 'bg-amber-50/80 ring-amber-200/70 text-amber-900',
        red: 'bg-rose-50/80 ring-rose-200/70 text-rose-900',
        slate: 'bg-white ring-slate-200/70 text-slate-700',
    };
    return (
        <div className={`flex flex-wrap items-center gap-x-4 gap-y-2 rounded-2xl px-4 py-3 ring-1 ${tones[tone] || tones.slate}`}>
            <div className="flex min-w-0 grow basis-0 items-start gap-2.5">
                {tone === 'slate'
                    ? <Loader2 size={16} className="mt-0.5 shrink-0 animate-spin text-slate-400" />
                    : <CircleAlert size={16} className="mt-0.5 shrink-0 opacity-70" />}
                <div className="min-w-0 text-[13px] leading-snug">
                    {title && <div className="font-semibold">{title}</div>}
                    {children && <div className="opacity-90">{children}</div>}
                </div>
            </div>
            {action}
        </div>
    );
};

const GpsStatus = ({ row, collectedAt }) => {
    if (row.gps_status === 'offline') {
        const silence = silenceLabel(row.signal_at, collectedAt);
        return (
            <span className="inline-flex items-center gap-1.5 font-medium text-rose-600">
                <span className="h-1.5 w-1.5 rounded-full bg-rose-500" />
                Офлайн{silence && <span className="font-normal text-rose-500/80">· {silence}</span>}
            </span>
        );
    }
    if (row.gps_status === 'none') {
        return (
            <span className="inline-flex items-center gap-1.5 font-medium text-amber-700">
                <span className="h-1.5 w-1.5 rounded-full bg-amber-500" />
                Нет GPS
            </span>
        );
    }
    return <span className="text-slate-600">{GPS_LABELS[row.gps_status] || ''}</span>;
};

const StateCell = ({ row, saving, onChange, emptyLabel = null }) => {
    const label = STATE_LABELS[row.state];
    const who = row.state_source === 'manual' && row.state_by_name
        ? `${row.state_by_name}${row.state_at ? `, ${shortTime(row.state_at)}` : ''}` : '';
    const text = label ? (
        <span className="min-w-0 truncate text-slate-800">
            {label}
            {row.state_source === 'address' && <span className="ml-1 text-[11.5px] text-slate-400">по адресу</span>}
        </span>
    ) : row.needs_check ? (
        <span className="rounded-full bg-amber-50 px-2 py-0.5 text-[12px] font-medium text-amber-800 ring-1 ring-amber-200/80">Уточнить</span>
    ) : emptyLabel ? (
        <span className="text-[13px] text-blue-600">{emptyLabel}</span>
    ) : (
        <span className="text-slate-300">—</span>
    );
    return (
        <span title={who || undefined} className="inline-flex max-w-full">
            <IosMenu
                label={`Состояние ${row.plate}`}
                align="left"
                disabled={saving}
                trigger={{
                    className: STATE_TRIGGER,
                    text: saving ? <Loader2 size={14} className="animate-spin text-slate-400" /> : text,
                }}
                items={[
                    ...STATES.map((state) => ({
                        key: state,
                        label: STATE_LABELS[state],
                        checked: row.state === state,
                        onSelect: () => onChange(row, state),
                    })),
                    row.state && {
                        key: 'clear', label: 'Не указано', separatorBefore: true, onSelect: () => onChange(row, null),
                    },
                ]}
            />
        </span>
    );
};

const RentalView = ({ apiBaseUrl, withAccessTokenHeader, showToast }) => {
    // showToast из App.jsx — новая функция на каждый рендер: держим её в ref,
    // иначе загрузка перезапускалась бы после каждого тоста.
    const toastRef = useRef(showToast);
    useEffect(() => { toastRef.current = showToast; }, [showToast]);
    const toast = useCallback((...args) => toastRef.current?.(...args), []);
    const headersRef = useRef(withAccessTokenHeader);
    useEffect(() => { headersRef.current = withAccessTokenHeader; }, [withAccessTokenHeader]);

    const api = useMemo(() => {
        const url = (path) => `${apiBaseUrl}/api/rental${path}`;
        const config = (extra) => ({ headers: headersRef.current ? headersRef.current() : {}, ...extra });
        return {
            get: (path, params) => axios.get(url(path), config({ params })),
            post: (path, body) => axios.post(url(path), body || {}, config()),
            put: (path, body) => axios.put(url(path), body || {}, config()),
            blob: (path) => axios.get(url(path), config({ responseType: 'blob' })),
        };
    }, [apiBaseUrl]);

    const [data, setData] = useState(null);
    const [error, setError] = useState('');
    // Выбранные дата и время отчёта; null — последний отчёт (так раздел открывается).
    const [pick, setPick] = useState(null);
    const [filter, setFilter] = useState('all');
    const [query, setQuery] = useState('');
    const [busy, setBusy] = useState({});

    const load = useCallback(async () => {
        try {
            const { data: screen } = await api.get('', pick ? { date: pick.date, kind: pick.kind } : undefined);
            setData(screen);
            setError('');
        } catch (requestError) {
            setError(errorOf(requestError, 'Раздел не открылся — попробуйте обновить страницу'));
        }
    }, [api, pick]);

    useEffect(() => { load(); }, [load]);
    useEffect(() => {
        const onVisible = () => { if (document.visibilityState === 'visible') load(); };
        document.addEventListener('visibilitychange', onVisible);
        return () => document.removeEventListener('visibilitychange', onVisible);
    }, [load]);

    // Пока идёт сбор — опрашиваем раз в 3 секунды: сбор занимает секунды, а не минуты.
    const report = data?.report || null;
    const collecting = Boolean(data?.collecting) || report?.status === 'collecting';
    useEffect(() => {
        if (!collecting) return undefined;
        const timer = setInterval(load, 3000);
        return () => clearInterval(timer);
    }, [collecting, load]);

    const rows = useMemo(() => data?.rows || [], [data]);
    // Счётчик рисует сам IosSegmented из поля count (у нуля — без цифры).
    const filters = useMemo(() => FILTERS.map((item) => (
        { value: item.value, label: item.label, count: rows.filter(item.test).length }
    )), [rows]);
    const activeFilter = FILTERS.find((item) => item.value === filter) || FILTERS[0];
    const shown = useMemo(
        () => rows.filter((row) => activeFilter.test(row) && matchesQuery(row, query)),
        [rows, activeFilter, query],
    );

    const run = async (key, action) => {
        setBusy((prev) => ({ ...prev, [key]: true }));
        try {
            await action();
        } finally {
            setBusy((prev) => ({ ...prev, [key]: false }));
        }
    };

    const collect = () => run('collect', async () => {
        try {
            await api.post('/reports');
            setPick(null);
            toast('Собираем отчёт — это меньше минуты', 'success');
        } catch (requestError) {
            toast(errorOf(requestError, 'Сбор не начался'), 'error');
        }
        await load();
    });

    const refresh = () => run('refresh', async () => {
        try {
            await api.post(`/reports/${report.id}/refresh`);
            toast('Обновляем отчёт. Состояния, которые вы поставили, останутся', 'success');
        } catch (requestError) {
            toast(errorOf(requestError, 'Обновление не началось'), 'error');
        }
        await load();
    });

    const changeState = (row, state) => run(`row:${row.id}`, async () => {
        try {
            const { data: result } = await api.put(`/rows/${row.id}/state`, { state });
            setData((prev) => (prev ? { ...prev, rows: prev.rows.map((item) => (item.id === row.id ? result.row : item)) } : prev));
        } catch (requestError) {
            toast(errorOf(requestError, 'Состояние не сохранилось'), 'error');
        }
    });

    const download = () => run('export', async () => {
        try {
            const response = await api.blob(`/reports/${report.id}/export`);
            const url = URL.createObjectURL(response.data);
            const link = document.createElement('a');
            link.href = url;
            link.download = exportFileName(report);
            document.body.appendChild(link);
            link.click();
            link.remove();
            setTimeout(() => URL.revokeObjectURL(url), 1000);
        } catch (requestError) {
            toast('Файл не выгрузился — попробуйте ещё раз', 'error');
        }
    });

    const shell = (children) => (
        <div className="relative mx-auto min-h-screen w-full max-w-[1400px] bg-gray-50 px-3 py-4 sm:px-5 sm:py-6 md:min-h-0"
             style={{ fontFamily: APPLE_FONT }}>
            {children}
        </div>
    );

    if (error && !data) {
        return shell(<div className={`${iosCard} p-6 text-center text-[13.5px] text-slate-600`}>{error}</div>);
    }
    if (!data) {
        return shell(
            <div className="flex items-center justify-center gap-2 py-16 text-[13px] text-slate-500">
                <Loader2 size={15} className="animate-spin" /> Открываем отчёт…
            </div>,
        );
    }

    const slot = data.slot || {};
    const selected = data.selected || null;
    const history = data.history || {};
    const today = String(data.server_time || '').slice(0, 10);
    const canRefresh = report && slot.report_id === report.id && slot.in_window && !collecting;
    const showsRent = Boolean(report?.shows_rent);
    const failed = report ? failedSources(report.sources) : [];

    const notices = [];
    if (slot.can_collect && !collecting) {
        notices.push(
            <Notice key="slot" tone="amber" title={`Отчёт за ${slot.kind_label} ещё не собран`}
                    action={<button type="button" className={TOOLBAR_BTN_PRIMARY} onClick={collect} disabled={busy.collect}>
                        {busy.collect ? <Loader2 size={15} className="animate-spin" /> : <RefreshCw size={15} />} Собрать
                    </button>}>
                {slot.in_window ? 'Планировщик его пропустил — соберите сейчас.' : 'Сбор вне ночи — для проверки раздела.'}
            </Notice>,
        );
    }
    if (collecting) {
        notices.push(<Notice key="collecting" tone="slate" title="Собираем отчёт">Yume и три GPS-системы — обычно меньше минуты.</Notice>);
    }
    if (report && report.status === 'failed' && !collecting) {
        notices.push(<Notice key="failed" tone="red" title="Отчёт не собран">{report.error || 'Источники не ответили.'}</Notice>);
    } else if (report && report.error && !collecting) {
        notices.push(<Notice key="refresh-error" tone="amber" title="Обновить не вышло">{report.error}</Notice>);
    }
    if (report && failed.length && report.status === 'ready' && !collecting) {
        notices.push(
            <Notice key="sources" tone="amber" title={`Не ответил: ${failed.map((item) => item.label).join(', ')}`}>
                {gpsSourceFailed(report.sources)
                    ? 'Машины из этой системы в отчёт не попали, а у машин парка «Нет GPS» может быть неточным. '
                    : 'Статус аренды неизвестен. '}
                {canRefresh ? 'Нажмите «Обновить».' : ''}
            </Notice>,
        );
    }

    const header = (
        <header className="mb-4 flex flex-wrap items-center justify-between gap-x-4 gap-y-3">
            <div className="flex min-w-0 items-center gap-3">
                {/* Плитка без градиента — владелец 27.10.2026: «не нравится градиент, его надо убрать». */}
                <span className="grid h-12 w-12 shrink-0 place-items-center rounded-[14px] bg-blue-600 text-white shadow-sm">
                    <Car size={24} strokeWidth={2} />
                </span>
                <div className="min-w-0 grow basis-0">
                    <h1 className="flex items-center gap-1.5 text-[22px] font-bold leading-tight tracking-tight text-slate-900">
                        Аренда авто
                        <IosHint text={HOW_IT_WORKS} label="Как собирается отчёт" />
                    </h1>
                    <p className="mt-0.5 text-[13px] text-slate-500">Ночной отчёт по арендным машинам</p>
                </div>
            </div>
            {/* Прошлые отчёты — по дате и времени (ТЗ п. 4: данные за предыдущие даты и время
                заполнения). Видно, как только есть хотя бы один отчёт. */}
            {history.first && selected && (
                <div className="flex flex-wrap items-center gap-2">
                    <IosDatePicker
                        ariaLabel="Дата отчёта"
                        value={selected.date}
                        min={history.first}
                        max={today || undefined}
                        onChange={(value) => value && setPick({ date: value, kind: selected.kind })}
                    />
                    <IosSegmented
                        value={selected.kind}
                        onChange={(kind) => setPick({ date: selected.date, kind })}
                        options={KIND_OPTIONS}
                        ariaLabel="Время отчёта"
                    />
                    {canRefresh && (
                        <button type="button" className={TOOLBAR_BTN} onClick={refresh} disabled={busy.refresh}>
                            {busy.refresh ? <Loader2 size={15} className="animate-spin" /> : <RefreshCw size={15} />} Обновить
                        </button>
                    )}
                    {report && report.status !== 'collecting' && rows.length > 0 && (
                        <button type="button" className={TOOLBAR_BTN} onClick={download} disabled={busy.export}>
                            {busy.export ? <Loader2 size={15} className="animate-spin" /> : <Download size={15} />} Excel
                        </button>
                    )}
                </div>
            )}
        </header>
    );

    if (!report) {
        return shell(
            <>
                {header}
                <div className="space-y-3">
                    {notices}
                    {selected && !collecting && (
                        <div className={`${iosCard} px-6 py-12 text-center`}>
                            <div className="text-[15px] font-semibold text-slate-800">За {pickLabel(selected.date, selected.kind)} отчёта нет</div>
                            <div className="mt-1 text-[13px] text-slate-500">Отчёты собираются каждую ночь в 01:00 и 07:00.</div>
                        </div>
                    )}
                    {!selected && !slot.can_collect && !collecting && (
                        <div className={`${iosCard} px-6 py-12 text-center`}>
                            <div className="text-[15px] font-semibold text-slate-800">Отчётов пока нет</div>
                            <div className="mt-1 text-[13px] text-slate-500">
                                Первый соберётся сам в {slot.next_kind_label || '01:00'}.
                            </div>
                        </div>
                    )}
                </div>
            </>,
        );
    }

    const columns = showsRent ? 7 : 6;

    return shell(
        <>
            {header}
            <div className="space-y-3">
                {notices}
                <div className={`${iosCard} overflow-hidden`}>
                    <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 px-4 py-3">
                        {/* Дата и время отчёта уже в выборе наверху — здесь только когда собран. */}
                        <div className="min-w-0 text-[13px] text-slate-600">
                            {report.collected_at ? `Собран в ${clockTime(report.collected_at)}` : 'Ещё не собран'}
                            {rows.length ? ` · машин ${rows.length}` : ''}
                        </div>
                        <div className="flex flex-wrap items-center gap-2">
                            {/* На телефоне четыре сегмента со счётчиками шире карточки — листаются вбок. */}
                            <div className="-mx-1 max-w-full overflow-x-auto px-1 [scrollbar-width:none]">
                                <IosSegmented value={activeFilter.value} onChange={setFilter} options={filters} ariaLabel="Какие машины показать" />
                            </div>
                            <label className="relative">
                                <Search size={14} className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
                                <input
                                    value={query}
                                    onChange={(event) => setQuery(event.target.value)}
                                    placeholder="Госномер или адрес"
                                    className="h-8 w-[200px] rounded-lg bg-slate-100/80 pl-8 pr-7 text-[13px] text-slate-800 outline-none ring-0 placeholder:text-slate-400 focus:bg-white focus:ring-2 focus:ring-blue-500/30"
                                />
                                {query && (
                                    <button type="button" aria-label="Очистить поиск" onClick={() => setQuery('')}
                                            className="absolute right-1.5 top-1/2 grid h-5 w-5 -translate-y-1/2 place-items-center rounded-full text-slate-400 hover:bg-slate-200">
                                        <X size={12} />
                                    </button>
                                )}
                            </label>
                        </div>
                    </div>

                    {/* Стол — от md; на телефоне карточки: семь столбцов в 360 px не читаются. */}
                    <div className="hidden overflow-x-auto md:block">
                        <table className="w-full min-w-[920px] text-left text-[13px]">
                            <thead>
                                <tr className="border-b border-slate-100 text-[11.5px] font-medium uppercase tracking-wide text-slate-400">
                                    <th className="px-4 py-2.5 font-medium">GPS-сайт</th>
                                    <th className="px-3 py-2.5 font-medium">Гос. номер</th>
                                    {showsRent && <th className="px-3 py-2.5 font-medium">Статус аренды</th>}
                                    <th className="px-3 py-2.5 font-medium">Статус GPS</th>
                                    <th className="px-3 py-2.5 font-medium">Состояние</th>
                                    <th className="px-3 py-2.5 font-medium">Последний трекинг</th>
                                    <th className="px-4 py-2.5 font-medium">Местоположение / адрес</th>
                                </tr>
                            </thead>
                            <tbody className="divide-y divide-slate-100">
                                {shown.map((row) => (
                                    <tr key={row.id} className="align-middle hover:bg-slate-50/60">
                                        <td className="whitespace-nowrap px-4 py-2 text-slate-600"
                                            title={row.gps_sites.length > 1 ? `Есть в: ${row.gps_sites.join(', ')}` : undefined}>
                                            {row.gps_site || <span className="text-slate-300">—</span>}
                                        </td>
                                        <td className="whitespace-nowrap px-3 py-2 font-semibold tabular-nums tracking-wide text-slate-900"
                                            title={row.model || undefined}>
                                            {row.plate}
                                        </td>
                                        {showsRent && (
                                            <td className="whitespace-nowrap px-3 py-2 text-slate-700">
                                                {RENT_LABELS[row.rent_status] || <span className="text-slate-300">—</span>}
                                            </td>
                                        )}
                                        <td className="whitespace-nowrap px-3 py-2"><GpsStatus row={row} collectedAt={report.collected_at} /></td>
                                        <td className="px-1 py-1"><StateCell row={row} saving={busy[`row:${row.id}`]} onChange={changeState} /></td>
                                        <td className="whitespace-nowrap px-3 py-2 tabular-nums text-slate-600">{shortTime(row.signal_at) || <span className="text-slate-300">—</span>}</td>
                                        <td className="max-w-[420px] px-4 py-2 text-slate-600">
                                            <span className="line-clamp-2" title={row.address || undefined}>{row.address || <span className="text-slate-300">—</span>}</span>
                                        </td>
                                    </tr>
                                ))}
                                {!shown.length && (
                                    <tr><td colSpan={columns} className="px-4 py-10 text-center text-[13px] text-slate-500">
                                        {rows.length ? 'Ничего не нашлось' : 'В отчёте пока нет машин'}
                                    </td></tr>
                                )}
                            </tbody>
                        </table>
                    </div>

                    <ul className="divide-y divide-slate-100 md:hidden">
                        {shown.map((row) => (
                            <li key={row.id} className="flex flex-col gap-1.5 px-4 py-3">
                                <div className="flex items-center justify-between gap-3">
                                    <span className="text-[15px] font-semibold tabular-nums tracking-wide text-slate-900">{row.plate}</span>
                                    <span className="text-[13px]"><GpsStatus row={row} collectedAt={report.collected_at} /></span>
                                </div>
                                <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[12.5px] text-slate-500">
                                    <span>{row.gps_site || 'Нет в GPS'}</span>
                                    {showsRent && row.rent_status && <span>{RENT_LABELS[row.rent_status]}</span>}
                                    {row.signal_at && <span className="tabular-nums">{shortTime(row.signal_at)}</span>}
                                </div>
                                {row.address && <div className="text-[13px] leading-snug text-slate-600">{row.address}</div>}
                                <div className="-ml-2">
                                    <StateCell row={row} saving={busy[`row:${row.id}`]} onChange={changeState} emptyLabel="Указать состояние" />
                                </div>
                            </li>
                        ))}
                        {!shown.length && (
                            <li className="px-4 py-10 text-center text-[13px] text-slate-500">
                                {rows.length ? 'Ничего не нашлось' : 'В отчёте пока нет машин'}
                            </li>
                        )}
                    </ul>
                </div>
            </div>
        </>,
    );
};

export default RentalView;
