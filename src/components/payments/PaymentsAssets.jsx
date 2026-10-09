import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import { ArrowRightLeft, Boxes, Loader2, Pencil, Search } from 'lucide-react';
import { iosBtnPrimary, iosBtnSecondary, iosCard, iosInput, IosModal } from '../ui/ios';
import IosDatePicker from '../ui/DatePicker';
import { CITY_OPTIONS } from './PaymentAssetsEditor';
import {
    ASSET_STATUS_META, ASSET_STATUS_OPTIONS, HINTS, LIST_PAGE_SIZE, RECORD_FORMS, fmtDate, fmtDateTime, fmtMoney, pageRange,
} from './paymentsMeta';
import {
    AmountInput, DATE_TRIGGER, ErrorBox, Field, FilePicker, FormSelect, NoticeBox, Pager, Row, SectionTitle, TonePill, UserSelect,
    downloadAttachment, errorText, multipart,
} from './paymentsUi';

/*
 * «Имущество» — реестр поставленного на учёт (ТЗ «Закуп и оплата», пп. 10–12).
 *
 * Карточка появляется из заявки: товар с категорией учёта «имущество» после
 * получения ставит на учёт ответственный за учёт имущества. Дальше имущество
 * живёт своей жизнью — его передают, перевозят, отдают в ремонт, списывают.
 *
 * Два правила п. 12. Данные учёта — отдельными полями (город, подразделение,
 * ответственный, место эксплуатации, инвентарный номер), по ним и фильтры.
 * История владельцев и мест не перезаписывается: каждое перемещение — новая
 * строка «было → стало», прежние остаются.
 *
 * Передача другому сотруднику идёт с актом приёма-передачи (п. 11).
 */

const SEARCH_DEBOUNCE_MS = 300;
const ACT_KINDS = [{ value: 'handover_act', label: 'Акт приёма-передачи' }];
const EMPTY_FILTERS = { status: '', city: '', department_id: null, category_id: null };

const statusPill = (asset) => {
    const meta = ASSET_STATUS_META[asset?.status];
    return meta ? <TonePill tone={meta.tone}>{meta.label}</TonePill> : null;
};

/* Что изменилось в перемещении — словами «было → стало». */
const moveLines = (move) => {
    if (move.kind === 'registered') {
        return [[move.to_responsible_name, move.to_department_name].filter(Boolean).join(', '),
            [move.to_city, move.to_location].filter(Boolean).join(', ')].filter(Boolean);
    }
    const lines = [];
    const pair = (label, from, to) => { if ((from || '') !== (to || '')) lines.push(`${label}: ${from || '—'} → ${to || '—'}`); };
    pair('Ответственный', move.from_responsible_name, move.to_responsible_name);
    pair('Подразделение', move.from_department_name, move.to_department_name);
    pair('Город', move.from_city, move.to_city);
    pair('Место', move.from_location, move.to_location);
    pair('Статус', move.from_status_label, move.to_status_label);
    return lines;
};

const MOVE_TITLES = { registered: 'Поставлено на учёт', moved: 'Перемещение', status: 'Смена статуса' };

const AssetCard = ({ assetId, apiBaseUrl, headers, dictionaries, users, showToast, onClose, onChanged, onOpenRequest }) => {
    const [data, setData] = useState(null);
    const [error, setError] = useState('');
    const [mode, setMode] = useState(null);         // 'move' | 'edit'
    const [draft, setDraft] = useState({});
    const [files, setFiles] = useState([]);
    const [busy, setBusy] = useState(false);

    const load = useCallback(async () => {
        if (!assetId) return;
        setError('');
        try {
            const response = await axios.get(`${apiBaseUrl}/api/payments/assets/${assetId}`, { headers: headers() });
            setData(response.data);
        } catch (err) {
            setError(errorText(err, 'Не удалось загрузить карточку'));
        }
    }, [apiBaseUrl, headers, assetId]);

    useEffect(() => { setData(null); setMode(null); load(); }, [load]);

    const asset = data?.asset;
    const canManage = Boolean(data?.can_manage);
    const set = (key, value) => setDraft((prev) => ({ ...prev, [key]: value }));

    const categoryOptions = useMemo(() => (dictionaries?.asset_categories || []).map((item) => ({ value: item.id, label: item.name })), [dictionaries]);
    const entityOptions = useMemo(() => (dictionaries?.legal_entities || []).map((item) => ({ value: item.id, label: item.name })), [dictionaries]);
    const departmentOptions = useMemo(() => (dictionaries?.departments || []).map((item) => ({ value: item.id, label: item.name })), [dictionaries]);
    // Город из карточки мог быть записан до появления списка — он остаётся выбираемым.
    const cityOptions = useMemo(() => (asset?.city && !CITY_OPTIONS.some((option) => option.value === asset.city)
        ? [{ value: asset.city, label: asset.city }, ...CITY_OPTIONS] : CITY_OPTIONS), [asset]);

    const startMove = () => {
        setDraft({
            responsible_user_id: asset.responsible_user_id, department_id: asset.department_id, city: asset.city,
            location: asset.location || '', status: asset.status, comment: '',
        });
        setFiles([]);
        setError('');
        setMode('move');
    };
    const startEdit = () => {
        setDraft({
            name: asset.name || '', category_id: asset.category_id, serial_number: asset.serial_number || '',
            inventory_number: asset.inventory_number || '', received_on: asset.received_on ? String(asset.received_on).slice(0, 10) : '',
            cost: asset.cost ?? 0, legal_entity_id: asset.legal_entity_id, note: asset.note || '',
        });
        setError('');
        setMode('edit');
    };

    const submit = async () => {
        setBusy(true);
        setError('');
        try {
            const response = mode === 'move'
                ? await axios.post(`${apiBaseUrl}/api/payments/assets/${assetId}/move`, multipart(draft, files), { headers: headers() })
                : await axios.patch(`${apiBaseUrl}/api/payments/assets/${assetId}`, { ...draft, received_on: draft.received_on || null }, { headers: headers() });
            setData((prev) => ({ ...prev, asset: response.data?.asset || prev.asset, moves: response.data?.moves || prev.moves }));
            showToast?.(mode === 'move' ? 'Перемещение записано' : 'Карточка сохранена', 'success');
            setMode(null);
            onChanged?.();
        } catch (err) {
            setError(errorText(err, 'Не удалось сохранить'));
        } finally {
            setBusy(false);
        }
    };

    const handingOver = mode === 'move' && asset && draft.responsible_user_id !== asset.responsible_user_id;

    const footer = mode ? (
        <>
            <button type="button" className={iosBtnSecondary} onClick={() => setMode(null)} disabled={busy}>Отмена</button>
            <button type="button" className={iosBtnPrimary} onClick={submit} disabled={busy}>
                {busy && <Loader2 size={14} className="animate-spin" />} {mode === 'move' ? 'Записать перемещение' : 'Сохранить'}
            </button>
        </>
    ) : (
        <>
            {canManage && asset && (
                <div className="mr-auto flex flex-wrap items-center gap-2">
                    <button type="button" className={iosBtnSecondary} onClick={startMove}><ArrowRightLeft size={14} /> Переместить</button>
                    <button type="button" className={iosBtnSecondary} onClick={startEdit}><Pencil size={14} /> Изменить описание</button>
                </div>
            )}
            <button type="button" className={iosBtnPrimary} onClick={onClose}>Готово</button>
        </>
    );

    return (
        <IosModal
            open={Boolean(assetId)}
            onClose={onClose}
            onBack={mode ? () => setMode(null) : null}
            title={mode === 'move' ? 'Перемещение имущества' : mode === 'edit' ? 'Описание имущества' : (asset?.name || 'Имущество')}
            subtitle={asset ? `инв. № ${asset.inventory_number}` : ''}
            maxWidth="max-w-2xl"
            footer={footer}
        >
            <div className="space-y-4">
                <ErrorBox text={error} />
                {!asset && !error && <div className="flex items-center justify-center gap-2 py-10 text-[13px] text-slate-500"><Loader2 size={15} className="animate-spin" /> Загружаем…</div>}

                {asset && !mode && (
                    <>
                        <div className={`${iosCard} px-4 py-2`}>
                            <Row label="Статус">{statusPill(asset)}</Row>
                            <Row label="Категория">{asset.category_name}</Row>
                            <Row label="Инвентарный №">{asset.inventory_number}</Row>
                            <Row label="Серийный №">{asset.serial_number}</Row>
                            <Row label="Ответственный">{asset.responsible_name}</Row>
                            <Row label="Подразделение">{asset.department_name}</Row>
                            <Row label="Город">{asset.city}</Row>
                            <Row label="Место эксплуатации">{asset.location}</Row>
                            <Row label="Компания-владелец">{asset.legal_entity_name}</Row>
                            <Row label="Дата получения">{asset.received_on ? fmtDate(asset.received_on) : ''}</Row>
                            <Row label="Стоимость">{asset.cost !== null && asset.cost !== undefined ? fmtMoney(asset.cost) : ''}</Row>
                            <Row label="Заявка">
                                {asset.request_id && (
                                    <button type="button" className="text-blue-600 hover:underline" onClick={() => onOpenRequest?.(asset.request_id)}>№{asset.request_id}</button>
                                )}
                            </Row>
                            <Row label="Примечание">{asset.note}</Row>
                        </div>

                        <section className="space-y-1.5">
                            <SectionTitle>История владельцев и мест</SectionTitle>
                            <div className={`${iosCard} divide-y divide-slate-100`}>
                                {(data.moves || []).map((move) => (
                                    <div key={move.id} className="px-4 py-2.5">
                                        <div className="flex flex-wrap items-baseline justify-between gap-x-3">
                                            <span className="text-[13.5px] font-medium text-slate-900">{MOVE_TITLES[move.kind] || 'Изменение'}</span>
                                            <span className="text-[12px] tabular-nums text-slate-500">{fmtDateTime(move.moved_at)}{move.moved_by_name ? ` · ${move.moved_by_name}` : ''}</span>
                                        </div>
                                        {moveLines(move).map((line) => <div key={line} className="break-words text-[13px] text-slate-700">{line}</div>)}
                                        {move.comment && move.kind !== 'registered' && <div className="break-words text-[12.5px] text-slate-500">{move.comment}</div>}
                                        {move.attachment_id && (
                                            <button
                                                type="button"
                                                className="mt-0.5 text-[12.5px] text-blue-600 hover:underline"
                                                onClick={() => downloadAttachment({ apiBaseUrl, headers, attachment: { id: move.attachment_id, file_name: move.attachment_name } })
                                                    .catch((err) => showToast?.(errorText(err, 'Не удалось скачать акт'), 'error'))}
                                            >
                                                Акт: {move.attachment_name || 'файл'}
                                            </button>
                                        )}
                                    </div>
                                ))}
                            </div>
                        </section>
                    </>
                )}

                {asset && mode === 'move' && (
                    <div className="space-y-3">
                        <div className="grid gap-3 sm:grid-cols-2">
                            <Field label="Ответственный сотрудник" required optionalMark={false} hint="За кем имущество числится. Оно появится в его профиле.">
                                <UserSelect users={users} value={draft.responsible_user_id} onChange={(v) => set('responsible_user_id', v)} />
                            </Field>
                            <Field label="Подразделение" required optionalMark={false}>
                                <FormSelect value={draft.department_id} onChange={(v) => set('department_id', v)} options={departmentOptions} placeholder="Подразделение" searchable ariaLabel="Подразделение" />
                            </Field>
                        </div>
                        <div className="grid gap-3 sm:grid-cols-2">
                            <Field label="Город" required optionalMark={false}>
                                <FormSelect value={draft.city} onChange={(v) => set('city', v)} options={cityOptions} placeholder="Выберите город" searchable ariaLabel="Город" />
                            </Field>
                            <Field label="Место эксплуатации" required optionalMark={false}>
                                <input className={iosInput} value={draft.location} onChange={(event) => set('location', event.target.value)} maxLength={300} />
                            </Field>
                        </div>
                        <Field label="Статус" required optionalMark={false} hint={HINTS.assetStatus}>
                            <FormSelect value={draft.status} onChange={(v) => set('status', v)} options={ASSET_STATUS_OPTIONS} ariaLabel="Статус имущества" />
                        </Field>
                        <Field as="div" label="Акт приёма-передачи" required={handingOver} optionalMark={!handingOver} hint="Подписанный акт: кто передал и кто принял. Обязателен, когда имущество переходит другому сотруднику.">
                            <FilePicker files={files} onChange={(next) => setFiles(next.slice(-1))} kinds={ACT_KINDS} label={files.length ? 'Заменить акт' : 'Приложить акт'} />
                        </Field>
                        {handingOver && !files.length && <NoticeBox tone="slate" text="Имущество передаётся другому сотруднику — приложите подписанный акт приёма-передачи." />}
                        <Field label="Комментарий">
                            <input className={iosInput} value={draft.comment} onChange={(event) => set('comment', event.target.value)} maxLength={1000} placeholder="Причина перемещения" />
                        </Field>
                    </div>
                )}

                {asset && mode === 'edit' && (
                    <div className="space-y-3">
                        <div className="grid gap-3 sm:grid-cols-2">
                            <Field label="Наименование" required optionalMark={false}><input className={iosInput} value={draft.name} onChange={(event) => set('name', event.target.value)} maxLength={300} /></Field>
                            <Field label="Категория" required optionalMark={false}>
                                <FormSelect value={draft.category_id} onChange={(v) => set('category_id', v)} options={categoryOptions} placeholder="Категория" ariaLabel="Категория имущества" />
                            </Field>
                        </div>
                        <div className="grid gap-3 sm:grid-cols-2">
                            <Field label="Серийный номер" required optionalMark={false}><input className={iosInput} value={draft.serial_number} onChange={(event) => set('serial_number', event.target.value)} maxLength={120} /></Field>
                            <Field label="Инвентарный номер" required optionalMark={false} hint="Двух одинаковых быть не может."><input className={iosInput} value={draft.inventory_number} onChange={(event) => set('inventory_number', event.target.value)} maxLength={64} /></Field>
                        </div>
                        <div className="grid gap-3 sm:grid-cols-3">
                            <Field label="Дата получения" required optionalMark={false}>
                                <IosDatePicker value={draft.received_on} onChange={(v) => set('received_on', v || '')} allowEmpty placeholder="Дата" triggerClassName={DATE_TRIGGER} ariaLabel="Дата получения" />
                            </Field>
                            <Field label="Стоимость, ₸" required optionalMark={false}><AmountInput value={draft.cost} onChange={(v) => set('cost', v)} ariaLabel="Стоимость" /></Field>
                            <Field label="Компания-владелец" required optionalMark={false}>
                                <FormSelect value={draft.legal_entity_id} onChange={(v) => set('legal_entity_id', v)} options={entityOptions} placeholder="Компания" ariaLabel="Компания-владелец" />
                            </Field>
                        </div>
                        <Field label="Примечание"><input className={iosInput} value={draft.note} onChange={(event) => set('note', event.target.value)} maxLength={2000} /></Field>
                        <NoticeBox tone="slate" text="Ответственного, подразделение, город, место и статус меняет «Переместить» — так прежние значения остаются в истории." />
                    </div>
                )}
            </div>
        </IosModal>
    );
};

const PaymentsAssets = ({ apiBaseUrl, headers, dictionaries, users, showToast, onOpenRequest }) => {
    const [items, setItems] = useState([]);
    const [total, setTotal] = useState(0);
    const [facets, setFacets] = useState({ cities: [], departments: [] });
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [search, setSearch] = useState('');
    const [query, setQuery] = useState('');
    const [filters, setFilters] = useState(EMPTY_FILTERS);
    const [openedId, setOpenedId] = useState(null);

    useEffect(() => {
        const timer = setTimeout(() => setQuery(search.trim()), SEARCH_DEBOUNCE_MS);
        return () => clearTimeout(timer);
    }, [search]);

    /* Страницами, как реестр заявок: номер страницы помнится вместе с отбором,
       и новый отбор начинается с первой. */
    const selectionKey = useMemo(() => {
        const params = new URLSearchParams();
        if (query) params.set('q', query);
        Object.entries(filters).forEach(([key, value]) => { if (value) params.set(key, String(value)); });
        return params.toString();
    }, [query, filters]);
    const [paging, setPaging] = useState({ key: '', page: 1 });
    const page = paging.key === selectionKey ? paging.page : 1;
    const [reloadKey, setReloadKey] = useState(0);
    const listRef = useRef(null);
    const goToPage = (next) => {
        setPaging({ key: selectionKey, page: next });
        const top = listRef.current?.getBoundingClientRect().top;
        if (top !== undefined && top < 0) listRef.current.scrollIntoView({ block: 'start', behavior: 'smooth' });
    };

    const ticket = useRef(0);
    const load = useCallback(async (pageNumber) => {
        const mine = ticket.current + 1;
        ticket.current = mine;
        setLoading(true);
        setError('');
        try {
            const params = new URLSearchParams(selectionKey);
            params.set('limit', String(LIST_PAGE_SIZE));
            params.set('offset', String((pageNumber - 1) * LIST_PAGE_SIZE));
            const response = await axios.get(`${apiBaseUrl}/api/payments/assets?${params}`, { headers: headers() });
            if (ticket.current !== mine) return;
            const body = response.data || {};
            setItems(body.items || []);
            setTotal(Number(body.total) || 0);
            setFacets(body.facets || { cities: [], departments: [] });
        } catch (err) {
            if (ticket.current !== mine) return;
            setError(errorText(err, 'Не удалось загрузить реестр имущества'));
        } finally {
            if (ticket.current === mine) setLoading(false);
        }
    }, [apiBaseUrl, headers, selectionKey]);

    useEffect(() => { load(page); }, [load, page, reloadKey]);

    const lastPage = pageRange(page, LIST_PAGE_SIZE, total).page;
    useEffect(() => {
        if (!loading && total > 0 && lastPage !== page) setPaging({ key: selectionKey, page: lastPage });
    }, [loading, total, lastPage, page, selectionKey]);

    const set = (patch) => setFilters((prev) => ({ ...prev, ...patch }));
    const narrowed = Boolean(query || Object.values(filters).some(Boolean));
    const cityOptions = useMemo(() => [{ value: '', label: 'Все города' }, ...(facets.cities || []).map((city) => ({ value: city, label: city }))], [facets]);
    const departmentOptions = useMemo(() => [{ value: '', label: 'Все подразделения' }, ...(facets.departments || []).map((item) => ({ value: item.id, label: item.name }))], [facets]);
    const categoryOptions = useMemo(() => [{ value: '', label: 'Все категории' }, ...(dictionaries?.asset_categories || []).map((item) => ({ value: item.id, label: item.name }))], [dictionaries]);

    return (
        <div>
            {/* Списки отбора — того же роста, что строка поиска рядом (FormSelect). */}
            <div className="grid gap-2 lg:grid-cols-[minmax(0,1fr)_repeat(4,minmax(0,176px))]">
                <div className="relative">
                    <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                    <input type="search" className={`${iosInput} pl-9`} placeholder="Название, инвентарный или серийный номер, ответственный…" aria-label="Поиск имущества" value={search} onChange={(event) => setSearch(event.target.value)} />
                </div>
                <FormSelect value={filters.status} onChange={(v) => set({ status: v || '' })} options={[{ value: '', label: 'Все статусы' }, ...ASSET_STATUS_OPTIONS]} ariaLabel="Статус" />
                <FormSelect value={filters.city} onChange={(v) => set({ city: v || '' })} options={cityOptions} searchable ariaLabel="Город" />
                <FormSelect value={filters.department_id} onChange={(v) => set({ department_id: v || null })} options={departmentOptions} searchable ariaLabel="Подразделение" />
                <FormSelect value={filters.category_id} onChange={(v) => set({ category_id: v || null })} options={categoryOptions} ariaLabel="Категория" />
            </div>

            {error && <div className="mt-3"><NoticeBox text={error} /></div>}

            <div ref={listRef} className="scroll-mt-4" />
            <div className={`${iosCard} mt-4 hidden overflow-x-auto md:block`}>
                <table className="w-full min-w-[980px] table-fixed border-collapse text-[13.5px]">
                    <colgroup>
                        <col style={{ width: 132 }} /><col /><col style={{ width: 208 }} /><col style={{ width: 224 }} />
                        <col style={{ width: 108 }} /><col style={{ width: 128 }} /><col style={{ width: 148 }} />
                    </colgroup>
                    <thead>
                        <tr className="border-b border-slate-200/70 bg-slate-50 text-left text-[11.5px] uppercase tracking-wider text-slate-500">
                            <th className="px-3.5 py-2.5 font-semibold">Инв. №</th>
                            <th className="px-3.5 py-2.5 font-semibold">Имущество</th>
                            <th className="px-3.5 py-2.5 font-semibold">Ответственный</th>
                            <th className="px-3.5 py-2.5 font-semibold">Город и место</th>
                            <th className="px-3.5 py-2.5 font-semibold">Получено</th>
                            <th className="px-3.5 py-2.5 text-right font-semibold">Стоимость</th>
                            <th className="px-3.5 py-2.5 font-semibold">Статус</th>
                        </tr>
                    </thead>
                    <tbody>
                        {items.map((asset, index) => (
                            <tr key={asset.id} data-asset={asset.id} onClick={() => setOpenedId(asset.id)} className={`h-[56px] cursor-pointer transition hover:bg-slate-50 ${index > 0 ? 'border-t border-slate-900/[0.06]' : ''} ${asset.status === 'written_off' ? 'text-slate-400' : ''}`}>
                                <td className="truncate px-3.5 py-2 tabular-nums text-slate-900">{asset.inventory_number}</td>
                                <td className="px-3.5 py-2">
                                    <div className="truncate text-slate-900">{asset.name}</div>
                                    <div className="truncate text-[12px] text-slate-500">{[asset.category_name, asset.serial_number && `серийный № ${asset.serial_number}`].filter(Boolean).join(' · ')}</div>
                                </td>
                                <td className="px-3.5 py-2">
                                    <div className="truncate text-slate-900">{asset.responsible_name || '—'}</div>
                                    <div className="truncate text-[12px] text-slate-500">{asset.department_name}</div>
                                </td>
                                <td className="px-3.5 py-2">
                                    <div className="truncate text-slate-900">{asset.city || '—'}</div>
                                    <div className="truncate text-[12px] text-slate-500">{asset.location}</div>
                                </td>
                                <td className="px-3.5 py-2 tabular-nums text-slate-700">{asset.received_on ? fmtDate(asset.received_on) : '—'}</td>
                                <td className="px-3.5 py-2 text-right tabular-nums text-slate-900">{asset.cost !== null && asset.cost !== undefined ? fmtMoney(asset.cost) : '—'}</td>
                                <td className="px-3.5 py-2">{statusPill(asset)}</td>
                            </tr>
                        ))}
                    </tbody>
                </table>
                {!items.length && !loading && (
                    <div className="flex flex-col items-center gap-2 px-4 py-12 text-center">
                        <Boxes size={22} className="text-slate-300" />
                        <p className="text-[13.5px] text-slate-500">{narrowed ? 'Ничего не нашлось — измените запрос или фильтры' : 'Имущества на учёте пока нет. Оно появляется из заявок на закуп товара с категорией «Имущество на учёт».'}</p>
                    </div>
                )}
            </div>

            <div className="mt-4 space-y-2 md:hidden">
                {items.map((asset) => (
                    <button key={asset.id} type="button" onClick={() => setOpenedId(asset.id)} className={`${iosCard} w-full p-3.5 text-left transition active:scale-[0.99]`}>
                        <div className="flex items-start justify-between gap-2">
                            <span className="min-w-0 text-[14px] font-medium text-slate-900">{asset.name}</span>
                            {statusPill(asset)}
                        </div>
                        <div className="mt-0.5 text-[12.5px] tabular-nums text-slate-500">инв. № {asset.inventory_number}</div>
                        <div className="mt-1 text-[12.5px] text-slate-600">{[asset.responsible_name, asset.city, asset.location].filter(Boolean).join(' · ')}</div>
                    </button>
                ))}
                {!items.length && !loading && (
                    <div className={`${iosCard} px-4 py-10 text-center text-[13.5px] text-slate-500`}>{narrowed ? 'Ничего не нашлось' : 'Имущества на учёте пока нет'}</div>
                )}
            </div>

            {loading && !items.length && <div className="mt-4 flex items-center justify-center gap-2 text-[13px] text-slate-500"><Loader2 size={15} className="animate-spin" /> Загружаем…</div>}
            <div className="mt-3">
                <Pager page={page} pageSize={LIST_PAGE_SIZE} total={total} loading={loading && items.length > 0} onPage={goToPage} forms={RECORD_FORMS} />
            </div>

            <AssetCard
                assetId={openedId}
                apiBaseUrl={apiBaseUrl}
                headers={headers}
                dictionaries={dictionaries}
                users={users}
                showToast={showToast}
                onClose={() => setOpenedId(null)}
                onChanged={() => setReloadKey((prev) => prev + 1)}
                onOpenRequest={(id) => { setOpenedId(null); onOpenRequest?.(id); }}
            />
        </div>
    );
};

export default PaymentsAssets;
