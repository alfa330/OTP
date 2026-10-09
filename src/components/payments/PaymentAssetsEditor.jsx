import React, { useMemo } from 'react';
import { Plus, X } from 'lucide-react';
import { iosBtnGhost, iosInput } from '../ui/ios';
import IosDatePicker from '../ui/DatePicker';
import { KAZAKHSTAN_CITY_OPTIONS, OPERATING_CITIES } from '../../utils/kazakhstanCities';
import { ASSET_STATUS_OPTIONS, HINTS, parseAmount } from './paymentsMeta';
import { AmountInput, DATE_TRIGGER, Field, FormSelect, UserSelect } from './paymentsUi';

/*
 * Карточки имущества при постановке на учёт (ТЗ «Закуп и оплата», п. 10.2).
 *
 * Обязательны все двенадцать полей: наименование, категория, серийный и
 * инвентарный номер, дата получения, стоимость, компания-владелец, город,
 * подразделение, ответственный сотрудник, место эксплуатации, статус.
 *
 * Город, подразделение и ответственный — выбором из списков, а не текстом
 * (п. 12: «в структурированном виде»): по ним строится инвентаризация и
 * профиль сотрудника, и «Алматы» с «г. Алматы» не должны разойтись в два города.
 *
 * Купили несколько единиц — у каждой своя карточка: серийный и инвентарный
 * номер у них разные. «Ещё такое же» копирует карточку без номеров.
 */

/* Города: сначала города присутствия компании, затем остальные города страны. */
export const CITY_OPTIONS = [
    ...OPERATING_CITIES.map((city) => ({ value: city, label: city, groupLabel: 'Города присутствия' })),
    ...KAZAKHSTAN_CITY_OPTIONS.filter((option) => !OPERATING_CITIES.includes(option.value)),
];

const newAsset = (patch = {}) => ({
    key: Math.random().toString(36).slice(2, 9), name: '', category_id: null, serial_number: '',
    inventory_number: '', received_on: '', cost: 0, legal_entity_id: null, city: null, department_id: null,
    responsible_user_id: null, location: '', status: 'in_use', ...patch,
});

/* Заготовки из заявки: по карточке на позицию, с её названием и ценой за единицу. */
export const assetsFromRequest = (request, items = []) => {
    const base = {
        received_on: request?.received_on ? String(request.received_on).slice(0, 10) : '',
        legal_entity_id: request?.legal_entity_id ?? null,
        department_id: request?.department_id ?? null,
    };
    const rows = items.length ? items : [{ name: request?.expense_name || '', unit_price: request?.amount || 0 }];
    return rows.map((item) => newAsset({ ...base, name: item.name || '', cost: parseAmount(item.unit_price) }));
};

export const assetPayload = (asset) => ({
    name: asset.name.trim(), category_id: asset.category_id, serial_number: asset.serial_number.trim(),
    inventory_number: asset.inventory_number.trim(), received_on: asset.received_on || null, cost: asset.cost,
    legal_entity_id: asset.legal_entity_id, city: asset.city, department_id: asset.department_id,
    responsible_user_id: asset.responsible_user_id, location: asset.location.trim(), status: asset.status,
});

const REQUIRED = [
    ['name', 'наименование'], ['category_id', 'категория'], ['serial_number', 'серийный номер'],
    ['inventory_number', 'инвентарный номер'], ['received_on', 'дата получения'],
    ['legal_entity_id', 'компания-владелец'], ['city', 'город'], ['department_id', 'подразделение'],
    ['responsible_user_id', 'ответственный'], ['location', 'место эксплуатации'], ['status', 'статус'],
];

/* Чего не хватает карточке — словами; пустой список — карточка полная. */
export const assetGaps = (asset) => REQUIRED
    .filter(([key]) => {
        const value = asset[key];
        return value === null || value === undefined || String(value).trim() === '';
    })
    .map(([, label]) => label);

const PaymentAssetsEditor = ({ assets, onChange, users, dictionaries }) => {
    const categoryOptions = useMemo(() => (dictionaries?.asset_categories || [])
        .map((item) => ({ value: item.id, label: item.name })), [dictionaries]);
    const entityOptions = useMemo(() => (dictionaries?.legal_entities || [])
        .map((item) => ({ value: item.id, label: item.name })), [dictionaries]);
    const departmentOptions = useMemo(() => (dictionaries?.departments || [])
        .map((item) => ({ value: item.id, label: item.name })), [dictionaries]);
    const update = (key, patch) => onChange(assets.map((asset) => (asset.key === key ? { ...asset, ...patch } : asset)));

    return (
        <div className="space-y-3">
            {assets.map((asset, index) => (
                <div key={asset.key} data-asset={index + 1} className="space-y-3 rounded-xl bg-slate-50 p-3">
                    <div className="flex items-center justify-between gap-2">
                        <span className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">Имущество {assets.length > 1 ? index + 1 : ''}</span>
                        {assets.length > 1 && (
                            <button type="button" aria-label={`Убрать имущество ${index + 1}`} className="grid h-7 w-7 place-items-center rounded-full text-slate-400 transition hover:bg-slate-200 hover:text-slate-700" onClick={() => onChange(assets.filter((row) => row.key !== asset.key))}>
                                <X size={14} />
                            </button>
                        )}
                    </div>
                    <div className="grid gap-3 sm:grid-cols-2">
                        <Field label="Наименование" required optionalMark={false}>
                            <input className={`${iosInput} bg-white`} value={asset.name} onChange={(event) => update(asset.key, { name: event.target.value })} maxLength={300} />
                        </Field>
                        <Field label="Категория" required optionalMark={false} hint="Категории имущества ведутся во вкладке «Справочники».">
                            <FormSelect value={asset.category_id} onChange={(value) => update(asset.key, { category_id: value })} options={categoryOptions} placeholder={categoryOptions.length ? 'Выберите категорию' : 'Заведите категории имущества'} disabled={!categoryOptions.length} ariaLabel="Категория имущества" />
                        </Field>
                    </div>
                    <div className="grid gap-3 sm:grid-cols-2">
                        <Field label="Серийный номер" required optionalMark={false} hint="Заводской номер с корпуса или коробки. У вещи без номера — «б/н».">
                            <input className={`${iosInput} bg-white`} value={asset.serial_number} onChange={(event) => update(asset.key, { serial_number: event.target.value })} maxLength={120} />
                        </Field>
                        <Field label="Инвентарный номер" required optionalMark={false} hint="Номер, под которым имущество числится в учёте. Двух одинаковых быть не может.">
                            <input className={`${iosInput} bg-white`} value={asset.inventory_number} onChange={(event) => update(asset.key, { inventory_number: event.target.value })} maxLength={64} />
                        </Field>
                    </div>
                    <div className="grid gap-3 sm:grid-cols-3">
                        <Field label="Дата получения" required optionalMark={false}>
                            <IosDatePicker value={asset.received_on} onChange={(value) => update(asset.key, { received_on: value || '' })} allowEmpty placeholder="Дата" triggerClassName={`${DATE_TRIGGER} !bg-white`} ariaLabel="Дата получения" />
                        </Field>
                        <Field label="Стоимость, ₸" required optionalMark={false}>
                            <AmountInput className="bg-white" value={asset.cost} onChange={(value) => update(asset.key, { cost: value })} ariaLabel="Стоимость" />
                        </Field>
                        <Field label="Компания-владелец" required optionalMark={false}>
                            <FormSelect value={asset.legal_entity_id} onChange={(value) => update(asset.key, { legal_entity_id: value })} options={entityOptions} placeholder="Компания" ariaLabel="Компания-владелец" />
                        </Field>
                    </div>
                    <div className="grid gap-3 sm:grid-cols-2">
                        <Field label="Город" required optionalMark={false}>
                            <FormSelect value={asset.city} onChange={(value) => update(asset.key, { city: value })} options={CITY_OPTIONS} placeholder="Выберите город" searchable ariaLabel="Город" />
                        </Field>
                        <Field label="Подразделение" required optionalMark={false}>
                            <FormSelect value={asset.department_id} onChange={(value) => update(asset.key, { department_id: value })} options={departmentOptions} placeholder="Подразделение" ariaLabel="Подразделение" />
                        </Field>
                    </div>
                    <div className="grid gap-3 sm:grid-cols-2">
                        <Field label="Ответственный сотрудник" required optionalMark={false} hint="За кем числится имущество. Оно появится в его профиле.">
                            <UserSelect users={users} value={asset.responsible_user_id} onChange={(value) => update(asset.key, { responsible_user_id: value })} placeholder="Выберите сотрудника" />
                        </Field>
                        <Field label="Место эксплуатации" required optionalMark={false} hint="Где имущество стоит: офис, кабинет, рабочее место.">
                            <input className={`${iosInput} bg-white`} value={asset.location} onChange={(event) => update(asset.key, { location: event.target.value })} maxLength={300} placeholder="Например: офис на Абая, каб. 12" />
                        </Field>
                    </div>
                    <Field label="Статус" required optionalMark={false} hint={HINTS.assetStatus}>
                        <FormSelect value={asset.status} onChange={(value) => update(asset.key, { status: value })} options={ASSET_STATUS_OPTIONS} ariaLabel="Статус имущества" />
                    </Field>
                </div>
            ))}
            <button
                type="button"
                className={`${iosBtnGhost} -ml-1`}
                onClick={() => {
                    // Копия последней карточки без того, что у каждой единицы своё.
                    const { key: _key, ...last } = assets[assets.length - 1] || {};
                    onChange([...assets, newAsset({ ...last, serial_number: '', inventory_number: '' })]);
                }}
            >
                <Plus size={14} /> Ещё такое же
            </button>
        </div>
    );
};

export default PaymentAssetsEditor;
