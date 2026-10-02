import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import { ChevronRight, Download, Loader2, Pencil, Plus, Search, X } from 'lucide-react';
import { APPLE_FONT, IosMenu, iosBtnPrimary, iosBtnSecondary, iosCard } from '../ui/ios';
import CustomSelect from '../ui/CustomSelect';
import ThermoboxMemo from './ThermoboxMemo';
import { AddOfficeSheet, ThermoboxRowSheet } from './ThermoboxRowSheet';
import { Dot, Pill, SectionTitle, StatusPill, TONES } from './thermoboxUi';
import {
    AVAILABILITY_LEGEND, COUNT_FIELDS, FIELD_LABELS, availabilityOf, changedFields, citiesOf, depositShort,
    draftErrors, exportFileName, latestUpdate, matchesRow, ordersLabel, periodShort, plural, tariffShort,
} from './thermoboxMeta';

/*
 * Раздел «Термокороба» (#363) — лист «Условия выдачи коробов» из Google-таблицы,
 * перенесённый в iCore. Вид «Светофор» в табличной форме — выбор владельца
 * (01.10.2026) из пяти вариантов «сделай заметнее».
 *
 * Строка — офис фронт-офиса. Цвет строки — наличие: зелёный — есть бесплатные
 * коробы, жёлтый — только Б/У или пакеты, красный — пусто. Условия — каждое
 * в своей колонке, как в листе; цветом отмечено только отличие от общего
 * правила. Сверху — «Обновлено: когда, кто», рядом — «Памятка для сотрудников».
 *
 * Правка двумя путями, под две работы:
 *   • карточка офиса (нажатие на строку) — всё про один офис, с историей; это
 *     единственный путь на телефоне;
 *   • «Изменить остатки» на компьютере — числа всех офисов прямо в таблице,
 *     одно сохранение на всё: так руководитель сверяет полку по сводке.
 * Обе отправляют одну и ту же пачку PUT /rows; сервер принимает её целиком
 * или не принимает вовсе.
 */

const errorOf = (requestError, fallback) => requestError?.response?.data?.error || fallback;

const COUNT_HEADERS = {
    free_boxes: <>Бесплатные<br />термокороба</>,
    thermo_bags: 'Термопакеты',
    used_boxes: <>Б/У<br />короба</>,
};

/* Плитки карточки на телефоне: три в ряд на 390 px, полные подписи не влезут. */
const COUNT_SHORT = {
    free_boxes: 'Бесплатные',
    thermo_bags: 'Термопакеты',
    used_boxes: 'Б/У',
};

/* Число в ячейке: бесплатные коробы — зелёной плашкой (по ним отправляют
   курьера), остальное — серой; ноль — бледным, без плашки. */
const CountCell = ({ value, accent }) => (
    <span className={`inline-flex min-w-[40px] justify-center rounded-lg px-2 py-1 text-[15px] font-bold tabular-nums ${
        value ? (accent ? TONES.green.cell : TONES.slate.cell) : 'text-slate-300'
    }`}>
        {value || 0}
    </span>
);

const CellInput = ({ value, onChange, changed, invalid, field, index, label }) => (
    <input
        className={`h-8 w-[64px] rounded-lg border-0 px-2 text-center text-[14px] font-semibold tabular-nums transition focus:outline-none focus:ring-2 focus:ring-blue-500/70 ${
            invalid
                ? 'bg-rose-50 text-rose-600 ring-1 ring-rose-300'
                : changed
                    ? 'bg-blue-50 text-blue-700 ring-1 ring-blue-200'
                    : 'bg-slate-100 text-slate-900'
        }`}
        inputMode="numeric"
        value={value}
        data-thermo-field={field}
        data-thermo-index={index}
        aria-label={label}
        onChange={(event) => onChange(event.target.value)}
        onFocus={(event) => event.target.select()}
        onKeyDown={(event) => {
            // Enter — к тому же полю следующего офиса, как в таблице Excel.
            if (event.key !== 'Enter') return;
            event.preventDefault();
            const next = document.querySelector(
                `[data-thermo-field="${field}"][data-thermo-index="${index + (event.shiftKey ? -1 : 1)}"]`);
            if (next) next.focus();
        }}
    />
);

/* Условия строкой для карточки на телефоне: обычное — текстом, отличие от
   общего правила — пилюлей, как в таблице. */
const ConditionsLine = ({ row }) => (
    <div className="mt-2.5 flex flex-wrap items-center gap-x-2 gap-y-1.5 text-[12.5px] leading-snug text-slate-600">
        {row.tariff === 'all' ? <Pill tone="blue" size="sm">Все тарифы</Pill> : <span>{tariffShort(row.tariff)}</span>}
        <span className="text-slate-300">·</span>
        <span>{row.min_orders ? `${ordersLabel(row.min_orders)} ${periodShort(row.period_days)}` : 'Без нормы заказов'}</span>
        <span className="text-slate-300">·</span>
        {row.deposit_tenge ? <span>депозит {depositShort(row.deposit_tenge)}</span> : <Pill tone="green" size="sm">Без депозита</Pill>}
        {row.special_condition && <Pill tone="amber" size="sm">{row.special_condition}</Pill>}
    </div>
);

const ThermoboxesView = ({ apiBaseUrl, withAccessTokenHeader, showToast }) => {
    const headers = useCallback(
        () => (withAccessTokenHeader ? withAccessTokenHeader() : {}),
        [withAccessTokenHeader],
    );
    // showToast из App.jsx — новая функция на каждый рендер: держим её в ref,
    // иначе загрузка перезапускалась бы после каждого тоста.
    const toastRef = useRef(showToast);
    useEffect(() => { toastRef.current = showToast; }, [showToast]);
    const toast = useCallback((...args) => toastRef.current?.(...args), []);

    const [data, setData] = useState(null);
    const [error, setError] = useState('');
    const [city, setCity] = useState('');
    const [query, setQuery] = useState('');
    const [editing, setEditing] = useState(false);
    const [drafts, setDrafts] = useState({});
    const [saving, setSaving] = useState(false);
    const [openedId, setOpenedId] = useState(null);
    const [adding, setAdding] = useState(false);
    const [exporting, setExporting] = useState(false);

    const load = useCallback(() => {
        setError('');
        return axios.get(`${apiBaseUrl}/api/thermoboxes`, { headers: headers() })
            .then((response) => setData(response.data || null))
            .catch((requestError) => setError(errorOf(requestError, 'Не удалось открыть раздел')));
    }, [apiBaseUrl, headers]);

    useEffect(() => { load(); }, [load]);

    const capabilities = data?.capabilities || {};
    const allRows = useMemo(() => data?.rows || [], [data]);
    const rows = useMemo(() => allRows.filter((row) => row.is_active), [allRows]);
    const hiddenRows = useMemo(() => allRows.filter((row) => !row.is_active), [allRows]);
    const cities = useMemo(() => citiesOf(rows), [rows]);
    const visible = useMemo(() => rows.filter((row) => matchesRow(row, city, query)), [rows, city, query]);
    const updated = useMemo(() => latestUpdate(rows), [rows]);
    const opened = allRows.find((row) => row.id === openedId) || null;
    const filtered = Boolean(city || query.trim());

    /* Свежие строки с сервера — на место старых; новые — в конец. */
    const mergeRows = useCallback((fresh) => {
        if (!fresh?.length) return;
        setData((prev) => {
            if (!prev) return prev;
            const byId = new Map(fresh.map((row) => [row.id, row]));
            const known = new Set(prev.rows.map((row) => row.id));
            const merged = prev.rows.map((row) => byId.get(row.id) || row);
            fresh.forEach((row) => { if (!known.has(row.id)) merged.push(row); });
            const directory = (prev.directory || []).filter(
                (office) => !fresh.some((row) => row.office_id === office.id));
            return { ...prev, rows: merged, directory };
        });
    }, []);

    /* ── Режим «Изменить остатки» ──────────────────────────────────────── */

    const draftValue = (row, field) => drafts[row.id]?.[field] ?? String(row[field] ?? 0);
    const setDraft = (row, field, value) => setDrafts((prev) => ({
        ...prev, [row.id]: { ...prev[row.id], [field]: value },
    }));

    const pending = useMemo(() => rows.map((row) => {
        const draft = drafts[row.id];
        if (!draft) return null;
        const changes = changedFields(row, draft, COUNT_FIELDS);
        const errors = draftErrors(draft, COUNT_FIELDS);
        if (!Object.keys(changes).length && !Object.keys(errors).length) return null;
        return { row, changes, errors };
    }).filter(Boolean), [rows, drafts]);

    const changedCount = pending.filter((item) => Object.keys(item.changes).length).length;
    const invalid = pending.some((item) => Object.keys(item.errors).length);

    const stopEditing = () => { setEditing(false); setDrafts({}); };

    const saveAll = async () => {
        if (!changedCount || invalid || saving) return;
        setSaving(true);
        try {
            const items = pending
                .filter((item) => Object.keys(item.changes).length)
                .map((item) => ({ id: item.row.id, version: item.row.version, ...item.changes }));
            const response = await axios.put(`${apiBaseUrl}/api/thermoboxes/rows`, { items }, { headers: headers() });
            mergeRows(response.data?.rows || []);
            const saved = response.data?.saved ?? items.length;
            toast(`Сохранено: ${saved} ${plural(saved, 'офис', 'офиса', 'офисов')}`, 'success');
            stopEditing();
        } catch (requestError) {
            const body = requestError?.response?.data;
            if (body?.code === 'THERMO_CONFLICT' && Array.isArray(body.rows)) {
                // Коллега успел раньше: его цифры — на экран, наши черновики по
                // этим офисам снимаем, остальные оставляем — их можно сохранить.
                mergeRows(body.rows);
                setDrafts((prev) => {
                    const next = { ...prev };
                    body.rows.forEach((row) => { delete next[row.id]; });
                    return next;
                });
            }
            toast(errorOf(requestError, 'Не удалось сохранить'), 'error');
        } finally {
            setSaving(false);
        }
    };

    const conflict = useCallback((fresh) => mergeRows(fresh), [mergeRows]);

    /* ── Выгрузка ──────────────────────────────────────────────────────── */

    const exportXlsx = async () => {
        if (exporting) return;
        setExporting(true);
        try {
            const params = new URLSearchParams();
            if (city) params.set('city', city);
            if (query.trim()) params.set('q', query.trim());
            const response = await axios.get(`${apiBaseUrl}/api/thermoboxes/export?${params}`, {
                headers: headers(), responseType: 'blob',
            });
            const url = URL.createObjectURL(response.data);
            const link = document.createElement('a');
            link.href = url;
            link.download = exportFileName();
            document.body.appendChild(link);
            link.click();
            link.remove();
            setTimeout(() => URL.revokeObjectURL(url), 1000);
        } catch (requestError) {
            toast('Не удалось выгрузить таблицу', 'error');
        } finally {
            setExporting(false);
        }
    };

    /* ── Экран ─────────────────────────────────────────────────────────── */

    /* Непрозрачный фон корня — на телефоне: там `.main-content` прозрачен, и в
       зазорах между карточками просвечивал фоновый логотип body серым пятном
       (тот же приём, что у «Моих часов» и «Профиля»). На компьютере цвет
       совпадает с фоном `.main-content`, а высота — своя. */
    const shell = (children) => (
        <div className="mx-auto min-h-screen w-full max-w-[1600px] bg-gray-50 px-3 py-4 sm:px-5 sm:py-6 md:min-h-0" style={{ fontFamily: APPLE_FONT }}>
            {children}
        </div>
    );

    if (error) {
        return shell(<div className={`${iosCard} p-6 text-center text-[13.5px] text-slate-600`}>{error}</div>);
    }
    if (!data) {
        return shell(
            <div className="flex items-center justify-center gap-2 py-16 text-[13px] text-slate-500">
                <Loader2 size={15} className="animate-spin" /> Открываем термокороба…
            </div>,
        );
    }
    if (data.schema_ready === false) {
        return shell(
            <div className="rounded-2xl bg-amber-50 px-4 py-3 text-[13px] text-amber-800 ring-1 ring-amber-200">
                Раздел разворачивается — он появится после перезапуска сервера.
            </div>,
        );
    }

    // IosMenu ждёт в icon сам компонент иконки, а не элемент: <Download /> здесь
    // роняет страницу целиком (React #130) в момент открытия меню.
    const menuItems = [
        { key: 'export', label: exporting ? 'Готовим файл…' : 'Выгрузить в Excel', icon: Download, onSelect: exportXlsx },
        capabilities.can_manage && { key: 'add', label: 'Добавить офис', icon: Plus, onSelect: () => setAdding(true) },
    ];

    const headerActions = (
        <>
            {capabilities.can_edit && rows.length > 0 && !editing && (
                <button type="button" className={`${iosBtnSecondary} hidden md:inline-flex`} onClick={() => setEditing(true)}>
                    <Pencil size={14} /> Изменить остатки
                </button>
            )}
            {!editing && <IosMenu items={menuItems} label="Ещё" />}
        </>
    );

    return shell(
        <>
            <SectionTitle updated={updated} actions={headerActions} />

            <div className="grid gap-4 2xl:grid-cols-[minmax(0,1fr)_380px] 2xl:items-start">
                <div className="min-w-0 space-y-3">
                    {rows.length > 0 && (
                        <div className="flex flex-wrap items-center justify-between gap-3">
                            <div className="flex min-w-0 flex-wrap items-center gap-2">
                                <div className="relative min-w-0 w-full sm:w-[300px]">
                                    <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                                    <input
                                        className="w-full rounded-xl border-0 bg-white py-2 pl-9 pr-8 text-[13.5px] text-slate-900 shadow-[0_1px_2px_rgba(15,23,42,0.04)] ring-1 ring-slate-200/70 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-blue-500/60"
                                        value={query}
                                        onChange={(event) => setQuery(event.target.value)}
                                        placeholder="Поиск"
                                        aria-label="Поиск: город, адрес или условие"
                                    />
                                    {query && (
                                        <button type="button" onClick={() => setQuery('')} aria-label="Очистить поиск"
                                                className="absolute right-2 top-1/2 grid h-5 w-5 -translate-y-1/2 place-items-center rounded-full bg-slate-300 text-white hover:bg-slate-400">
                                            <X size={11} strokeWidth={3} />
                                        </button>
                                    )}
                                </div>
                                <CustomSelect
                                    value={city}
                                    onChange={setCity}
                                    options={[{ value: '', label: 'Все города' }, ...cities.map((name) => ({ value: name, label: name }))]}
                                    variant="ios"
                                    className="w-[170px]"
                                    ariaLabel="Город"
                                />
                                {filtered && (
                                    <span className="text-[12.5px] tabular-nums text-slate-500">{visible.length} из {rows.length}</span>
                                )}
                            </div>
                            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[12px] text-slate-500">
                                {AVAILABILITY_LEGEND.map((item) => (
                                    <span key={item.key} className="inline-flex items-center gap-1.5"><Dot tone={item.tone} />{item.label}</span>
                                ))}
                            </div>
                        </div>
                    )}

                    {/* Компьютер: таблица, колонки — как в листе. */}
                    <div className={`${iosCard} hidden overflow-hidden md:block`}>
                        <div className="thin-scroll overflow-x-auto">
                            <table className="w-full min-w-[980px] text-left text-[13px]">
                                <thead className="border-b border-slate-200/70 bg-slate-50/80 text-[12px] font-semibold text-slate-500">
                                    <tr>
                                        <th className="py-3 pl-5 pr-3">Офис</th>
                                        <th className="px-3 py-3">Наличие</th>
                                        {COUNT_FIELDS.map((field) => (
                                            <th key={field} className="px-2 py-3 text-center leading-tight">{COUNT_HEADERS[field]}</th>
                                        ))}
                                        <th className="px-3 py-3">Тариф</th>
                                        <th className="px-3 py-3">Заказы</th>
                                        <th className="px-3 py-3">Депозит</th>
                                        <th className="px-3 py-3">Отдельное условие</th>
                                        {!editing && <th className="w-8" aria-hidden="true" />}
                                    </tr>
                                </thead>
                                <tbody>
                                    {visible.map((row, index) => {
                                        const state = availabilityOf(row);
                                        const errors = editing ? draftErrors(drafts[row.id], COUNT_FIELDS) : {};
                                        const changes = editing && drafts[row.id] ? changedFields(row, drafts[row.id], COUNT_FIELDS) : {};
                                        return (
                                            <tr
                                                key={row.id}
                                                onClick={editing ? undefined : () => setOpenedId(row.id)}
                                                className={`border-l-4 ${TONES[state.tone].edge} ${index ? 'border-t border-t-slate-100' : ''} ${editing ? '' : 'group cursor-pointer transition hover:bg-slate-50/80'}`}
                                            >
                                                <td className="py-3 pl-4 pr-3 align-middle">
                                                    <div className="text-[14px] font-semibold text-slate-900">{row.city}</div>
                                                    <div className="text-[12px] text-slate-500">{row.address || row.name}</div>
                                                </td>
                                                <td className="px-3 py-3 align-middle"><StatusPill row={row} /></td>
                                                {COUNT_FIELDS.map((field) => (
                                                    <td key={field} className="px-2 py-2.5 text-center align-middle">
                                                        {editing ? (
                                                            <CellInput
                                                                value={draftValue(row, field)}
                                                                onChange={(value) => setDraft(row, field, value)}
                                                                changed={field in changes}
                                                                invalid={Boolean(errors[field])}
                                                                field={field}
                                                                index={index}
                                                                label={`${row.city}: ${FIELD_LABELS[field]}`}
                                                            />
                                                        ) : <CountCell value={row[field]} accent={field === 'free_boxes'} />}
                                                    </td>
                                                ))}
                                                <td className="whitespace-nowrap px-3 py-3 align-middle text-slate-700">
                                                    {row.tariff === 'all' ? <Pill tone="blue">Все тарифы</Pill> : tariffShort(row.tariff)}
                                                </td>
                                                <td className="whitespace-nowrap px-3 py-3 align-middle">
                                                    <div className="text-slate-700">{ordersLabel(row.min_orders)}</div>
                                                    {row.min_orders > 0 && <div className="text-[12px] text-slate-500">{periodShort(row.period_days)}</div>}
                                                </td>
                                                <td className="whitespace-nowrap px-3 py-3 align-middle tabular-nums text-slate-700">
                                                    {row.deposit_tenge ? depositShort(row.deposit_tenge) : <Pill tone="green">Без депозита</Pill>}
                                                </td>
                                                <td className="px-3 py-3 align-middle">
                                                    {row.special_condition ? <Pill tone="amber">{row.special_condition}</Pill> : null}
                                                </td>
                                                {!editing && (
                                                    <td className="pr-3 align-middle text-slate-300 transition group-hover:text-slate-400">
                                                        <ChevronRight size={16} />
                                                    </td>
                                                )}
                                            </tr>
                                        );
                                    })}
                                </tbody>
                            </table>
                        </div>
                        {!visible.length && (
                            <div className="px-4 py-12 text-center text-[13.5px] text-slate-500">
                                {rows.length ? 'Ничего не нашлось' : 'В таблице пока нет офисов'}
                            </div>
                        )}
                    </div>

                    {/* Телефон: карточки с той же цветной полосой наличия. */}
                    <div className="space-y-2 md:hidden">
                        {visible.map((row) => {
                            const state = availabilityOf(row);
                            return (
                                <button key={row.id} type="button" onClick={() => setOpenedId(row.id)}
                                        className={`${iosCard} block w-full border-l-4 ${TONES[state.tone].edge} p-3.5 text-left transition active:scale-[0.99]`}>
                                    <div className="flex items-start justify-between gap-2">
                                        <div className="min-w-0 grow basis-0">
                                            <div className="text-[15px] font-semibold text-slate-900">{row.city}</div>
                                            <div className="truncate text-[12.5px] text-slate-500">{row.address || row.name}</div>
                                        </div>
                                        <StatusPill row={row} compact withCount />
                                    </div>
                                    {/* flex, а не grid-cols-3: мобильная оболочка
                                        перекладывает любую трёхколоночную сетку в две
                                        колонки (mobile-shell.css, «плитки показателей»),
                                        а три коротких числа читаются одной строкой. */}
                                    <div className="mt-3 flex gap-2">
                                        {COUNT_FIELDS.map((field) => (
                                            <div key={field} className={`min-w-0 grow basis-0 rounded-xl px-2.5 py-2 ${
                                                field === 'free_boxes' && row[field] ? 'bg-emerald-50' : 'bg-slate-50'
                                            }`}>
                                                <div className="truncate text-[11px] text-slate-500">{COUNT_SHORT[field]}</div>
                                                <div className={`text-[17px] font-bold tabular-nums ${
                                                    row[field] ? (field === 'free_boxes' ? 'text-emerald-700' : 'text-slate-900') : 'text-slate-300'
                                                }`}>
                                                    {row[field] || 0}
                                                </div>
                                            </div>
                                        ))}
                                    </div>
                                    <ConditionsLine row={row} />
                                </button>
                            );
                        })}
                        {!visible.length && (
                            <div className={`${iosCard} px-4 py-12 text-center text-[13.5px] text-slate-500`}>
                                {rows.length ? 'Ничего не нашлось' : 'В таблице пока нет офисов'}
                            </div>
                        )}
                    </div>

                    {/* По центру, а не справа: в правом нижнем углу живёт кнопка
                        помощника портала, и панель уходила бы под неё. */}
                    {editing && (
                        <div className="sticky bottom-4 z-20 flex justify-center">
                            <div className="flex items-center gap-2 rounded-2xl bg-white/90 p-2 pl-4 shadow-[0_8px_30px_rgba(15,23,42,0.14)] ring-1 ring-slate-200/80 backdrop-blur-xl">
                                <span className={`mr-1 text-[13px] tabular-nums ${invalid ? 'text-rose-600' : 'text-slate-500'}`}>
                                    {invalid
                                        ? 'Только целые числа, не меньше нуля'
                                        : changedCount
                                            ? `Изменено: ${changedCount} ${plural(changedCount, 'офис', 'офиса', 'офисов')}`
                                            : 'Правьте числа прямо в таблице'}
                                </span>
                                <button type="button" className={iosBtnSecondary} onClick={stopEditing} disabled={saving}>Отменить</button>
                                <button type="button" className={iosBtnPrimary} onClick={saveAll} disabled={!changedCount || invalid || saving}>
                                    {saving && <Loader2 size={15} className="animate-spin" />}
                                    Сохранить
                                </button>
                            </div>
                        </div>
                    )}

                    {!editing && rows.length > 0 && (
                        <p className="hidden px-1 text-[12px] text-slate-400 md:block">
                            Нажмите на офис — откроются все цифры, условия и история изменений.
                        </p>
                    )}

                    {capabilities.can_manage && hiddenRows.length > 0 && (
                        <p className="px-1 text-[12.5px] text-slate-500">
                            Убраны из таблицы:{' '}
                            {hiddenRows.map((row, index) => (
                                <React.Fragment key={row.id}>
                                    {index > 0 && ', '}
                                    <button type="button" className="underline decoration-slate-300 underline-offset-2 hover:text-slate-700"
                                            onClick={() => setOpenedId(row.id)}>
                                        {row.city} · {row.address || row.name}
                                    </button>
                                </React.Fragment>
                            ))}
                        </p>
                    )}
                </div>

                <ThermoboxMemo
                    memo={data.memo}
                    canEdit={Boolean(capabilities.can_edit_memo)}
                    apiBaseUrl={apiBaseUrl}
                    headers={headers}
                    onSaved={(memo) => memo && setData((prev) => (prev ? { ...prev, memo } : prev))}
                    showToast={toast}
                />
            </div>

            <ThermoboxRowSheet
                open={Boolean(opened)}
                row={opened}
                onClose={() => setOpenedId(null)}
                capabilities={capabilities}
                apiBaseUrl={apiBaseUrl}
                headers={headers}
                onSaved={mergeRows}
                onConflict={conflict}
                showToast={toast}
            />
            {capabilities.can_manage && (
                <AddOfficeSheet
                    open={adding}
                    onClose={() => setAdding(false)}
                    directory={data.directory || []}
                    apiBaseUrl={apiBaseUrl}
                    headers={headers}
                    onAdded={mergeRows}
                    showToast={toast}
                />
            )}
        </>,
    );
};

export default ThermoboxesView;
