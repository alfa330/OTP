import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import { Loader2, Plus, X } from 'lucide-react';
import { iosBtnGhost, iosBtnPrimary, iosBtnSecondary, iosInput, IosModal, IosSegmented, IosSection } from '../ui/ios';
import CustomSelect from '../ui/CustomSelect';
import IosDatePicker from '../ui/DatePicker';
import {
    CONTRACT_THRESHOLD, SOURCE_OPTIONS, TYPE_OPTIONS, fmtDate, fmtMoney, itemsTotal, parseAmount,
} from './paymentsMeta';
import {
    AmountInput, ErrorBox, Field, FilePicker, NoticeBox, UserSelect, appendPayloadFiles, errorText,
} from './paymentsUi';

/*
 * Форма заявки на закуп — создание и правка.
 *
 * На виду только то, без чего заявка не заводится: что закупаем (позиции с
 * количеством и ценой — п. 10 дополнения запрещает «хоз. товары» одной
 * строкой), у кого (контрагент), по какой статье (категория), откуда платим
 * (источник) и какого типа платёж. Остальное — чипами «+ …», как в форме
 * задачи: филиал, период, номер карты, срок, юр. лицо, договор, примечания.
 *
 * Руководитель подставляется сам (supervisor_id → глава отдела) и показан
 * строкой; выбрать другого можно, но обычно незачем. Без руководителя шаг 2
 * будет пропущен — об этом форма говорит прямо.
 *
 * Файлы (реестр поставщиков, КП) прикладываются здесь же при создании: без
 * них шаг 1 не отпишется, и заставлять человека открывать карточку ради этого
 * незачем. При правке файлы живут в карточке.
 */

const EXTRA_FIELDS = [
    { key: 'branch', label: 'Филиал / регион' },
    { key: 'payment_period', label: 'Период оплаты' },
    { key: 'due_on', label: 'Срок оплаты' },
    { key: 'legal_entity_id', label: 'Юр. лицо' },
    { key: 'contract_id', label: 'Договор' },
    { key: 'card_number', label: 'Номер карты' },
    { key: 'notes', label: 'Примечания' },
];

const newItem = () => ({ key: Math.random().toString(36).slice(2, 9), name: '', quantity: 1, unit: '', unit_price: 0 });

const emptyDraft = (me, manager) => ({
    expense_name: '',
    project_id: null,
    branch: '',
    category_id: null,
    subcategory_id: null,
    counterparty_id: null,
    legal_entity_id: null,
    contract_id: null,
    payment_source: 'too',
    payment_type: 'one_time',
    payment_period: '',
    card_number: '',
    notes: '',
    due_on: '',
    department_id: me?.department_id || null,
    manager_id: manager?.id || null,
    items: [newItem()],
});

const draftFromRequest = (request, items) => ({
    expense_name: request.expense_name || '',
    project_id: request.project_id ?? null,
    branch: request.branch || '',
    category_id: request.category_id ?? null,
    subcategory_id: request.subcategory_id ?? null,
    counterparty_id: request.counterparty_id ?? null,
    legal_entity_id: request.legal_entity_id ?? null,
    contract_id: request.contract_id ?? null,
    payment_source: request.payment_source || 'too',
    payment_type: request.payment_type || 'one_time',
    payment_period: request.payment_period || '',
    card_number: request.card_number || '',
    notes: request.notes || '',
    due_on: request.due_on ? String(request.due_on).slice(0, 10) : '',
    department_id: request.department_id ?? null,
    manager_id: request.manager_id ?? null,
    items: (items && items.length ? items : [{}]).map((item) => ({
        key: Math.random().toString(36).slice(2, 9),
        name: item.name || '',
        quantity: parseAmount(item.quantity) || 1,
        unit: item.unit || '',
        unit_price: parseAmount(item.unit_price),
    })),
});

const dateTrigger = 'flex w-full items-center gap-2 rounded-xl bg-slate-100 px-3.5 py-2.5 '
    + 'text-[14px] tabular-nums text-slate-900 border-0 transition hover:bg-slate-200/70 '
    + 'focus:outline-none focus:ring-2 focus:ring-blue-500/70 [&>span]:flex-1 [&>span]:text-left';

const HISTORY_DEBOUNCE_MS = 400;

const PaymentRequestForm = ({
    open, onClose, apiBaseUrl, headers, dictionaries, users, me, request = null, items = [], onSaved, showToast,
    stepFiles = [],
}) => {
    const editing = Boolean(request);
    const [draft, setDraft] = useState(() => emptyDraft(me, dictionaries?.manager));
    const [extras, setExtras] = useState([]);
    const [files, setFiles] = useState([]);
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState('');
    const [managerEditing, setManagerEditing] = useState(false);
    const [history, setHistory] = useState([]);
    const [newCounterparty, setNewCounterparty] = useState('');
    const [creatingCounterparty, setCreatingCounterparty] = useState(false);
    const [localDicts, setLocalDicts] = useState(dictionaries);

    useEffect(() => { setLocalDicts(dictionaries); }, [dictionaries]);

    const toastRef = useRef(showToast);
    useEffect(() => { toastRef.current = showToast; }, [showToast]);

    useEffect(() => {
        if (!open) return;
        setError('');
        setFiles([]);
        setManagerEditing(false);
        setNewCounterparty('');
        if (request) {
            const next = draftFromRequest(request, items);
            setDraft(next);
            setExtras(EXTRA_FIELDS.map((field) => field.key).filter((key) => {
                const value = next[key];
                return value !== '' && value !== null && value !== undefined;
            }));
        } else {
            setDraft(emptyDraft(me, dictionaries?.manager));
            setExtras([]);
        }
    }, [open, request, items, me, dictionaries]);

    const set = useCallback((key, value) => setDraft((prev) => ({ ...prev, [key]: value })), []);

    const total = useMemo(() => itemsTotal(draft.items), [draft.items]);

    const categories = useMemo(() => (localDicts?.categories || []).filter((item) => !item.parent_id), [localDicts]);
    const subcategories = useMemo(() => (localDicts?.categories || [])
        .filter((item) => item.parent_id && item.parent_id === draft.category_id), [localDicts, draft.category_id]);
    const counterpartyOptions = useMemo(() => (localDicts?.counterparties || [])
        .map((item) => ({ value: item.id, label: item.bin ? `${item.name} · БИН ${item.bin}` : item.name })), [localDicts]);
    const contractOptions = useMemo(() => (localDicts?.contracts || [])
        .filter((item) => item.counterparty_id === draft.counterparty_id)
        .map((item) => ({
            value: item.id,
            label: `№${item.number}${item.ends_on ? ` до ${fmtDate(item.ends_on)}` : ''}${item.status !== 'active' ? ' · недействующий' : ''}`,
        })), [localDicts, draft.counterparty_id]);
    const projectOptions = useMemo(() => [{ value: null, label: 'Без проекта' },
        ...(localDicts?.projects || []).map((item) => ({ value: item.id, label: item.name }))], [localDicts]);
    const legalEntityOptions = useMemo(() => (localDicts?.legal_entities || [])
        .map((item) => ({ value: item.id, label: item.name })), [localDicts]);
    const departmentOptions = useMemo(() => (localDicts?.departments || [])
        .map((item) => ({ value: item.id, label: item.name })), [localDicts]);

    const manager = useMemo(() => (users || []).find((user) => user.id === draft.manager_id) || null, [users, draft.manager_id]);
    const contractNeeded = total > CONTRACT_THRESHOLD && draft.counterparty_id;

    // Справка «История оплат»: похожие ранее оплаченные заявки — по контрагенту,
    // категории и названию. Спрашивается сама, без кнопки (п. 9 дополнения).
    useEffect(() => {
        if (!open) return undefined;
        if (!draft.counterparty_id && !draft.category_id && (draft.expense_name || '').trim().length < 3) {
            setHistory([]);
            return undefined;
        }
        const timer = setTimeout(() => {
            const params = new URLSearchParams();
            if (draft.counterparty_id) params.set('counterparty_id', draft.counterparty_id);
            if (draft.category_id) params.set('category_id', draft.category_id);
            if (draft.subcategory_id) params.set('subcategory_id', draft.subcategory_id);
            if ((draft.expense_name || '').trim().length >= 3) params.set('name', draft.expense_name.trim());
            if (request?.id) params.set('exclude', request.id);
            axios.get(`${apiBaseUrl}/api/payments/history?${params}`, { headers: headers() })
                .then((response) => setHistory(response.data?.items || []))
                .catch(() => setHistory([]));
        }, HISTORY_DEBOUNCE_MS);
        return () => clearTimeout(timer);
    }, [open, apiBaseUrl, headers, draft.counterparty_id, draft.category_id, draft.subcategory_id, draft.expense_name, request?.id]);

    const addExtra = (key) => setExtras((prev) => (prev.includes(key) ? prev : [...prev, key]));
    const removeExtra = (key) => {
        setExtras((prev) => prev.filter((item) => item !== key));
        set(key, key.endsWith('_id') ? null : '');
    };

    const updateItem = (key, patch) => setDraft((prev) => ({
        ...prev,
        items: prev.items.map((item) => (item.key === key ? { ...item, ...patch } : item)),
    }));

    const createCounterparty = async () => {
        const name = newCounterparty.trim();
        if (!name) return;
        setCreatingCounterparty(true);
        try {
            const response = await axios.post(`${apiBaseUrl}/api/payments/dictionaries/counterparties`, { name }, { headers: headers() });
            const id = response.data?.id;
            const refreshed = await axios.get(`${apiBaseUrl}/api/payments/dictionaries`, { headers: headers() });
            setLocalDicts(refreshed.data);
            set('counterparty_id', id);
            setNewCounterparty('');
        } catch (err) {
            toastRef.current?.(errorText(err, 'Не удалось добавить контрагента'), 'error');
        } finally {
            setCreatingCounterparty(false);
        }
    };

    const validate = () => {
        if (!draft.expense_name.trim()) return 'Укажите наименование расхода';
        const filled = draft.items.filter((item) => item.name.trim());
        if (!filled.length) return 'Добавьте хотя бы одну позицию';
        if (filled.some((item) => parseAmount(item.quantity) <= 0)) return 'Количество в позиции должно быть больше нуля';
        if (total <= 0) return 'Сумма заявки должна быть больше нуля';
        if (!draft.counterparty_id) return 'Выберите контрагента';
        if (!draft.category_id) return 'Выберите категорию расхода';
        if (!editing && !files.length) return 'Приложите реестр поставщиков или КП — без них шаг 1 не пройти';
        return '';
    };

    const submit = async () => {
        const problem = validate();
        if (problem) { setError(problem); return; }
        setSaving(true);
        setError('');
        const payload = {
            ...draft,
            items: draft.items.filter((item) => item.name.trim()).map((item) => ({
                name: item.name.trim(), quantity: parseAmount(item.quantity) || 1, unit: item.unit || null,
                unit_price: parseAmount(item.unit_price),
            })),
        };
        try {
            let response;
            if (editing) {
                response = await axios.patch(`${apiBaseUrl}/api/payments/requests/${request.id}`, payload, { headers: headers() });
            } else {
                const form = new FormData();
                form.append('payload', JSON.stringify({ ...payload, submit: true }));
                appendPayloadFiles(form, files);
                response = await axios.post(`${apiBaseUrl}/api/payments/requests`, form, { headers: headers() });
            }
            const data = response.data || {};
            if (data.warnings?.length) toastRef.current?.(data.warnings.join('. '), 'info');
            else toastRef.current?.(editing ? 'Заявка сохранена' : (data.submitted ? 'Заявка отправлена на согласование' : 'Заявка создана'), 'success');
            onSaved?.(data);
            onClose?.();
        } catch (err) {
            setError(errorText(err, 'Не удалось сохранить заявку'));
        } finally {
            setSaving(false);
        }
    };

    const kinds = useMemo(() => (stepFiles.length ? stepFiles : ['supplier_registry', 'offer', 'other'])
        .map((value) => ({ value, label: ({ supplier_registry: 'Реестр поставщиков', offer: 'Коммерческое предложение', other: 'Другое' })[value] || value })), [stepFiles]);

    return (
        <IosModal
            open={open}
            onClose={onClose}
            title={editing ? `Заявка №${request.id} — правка` : 'Новая заявка на закуп'}
            subtitle={editing ? request.expense_name : 'Шаг 1 из 12 · Согласование закупки'}
            maxWidth="max-w-2xl"
            footer={(
                <>
                    <button type="button" className={iosBtnSecondary} onClick={onClose} disabled={saving}>Отмена</button>
                    <button type="button" className={iosBtnPrimary} onClick={submit} disabled={saving}>
                        {saving && <Loader2 size={14} className="animate-spin" />}
                        {editing ? 'Сохранить' : 'Создать и отправить на согласование'}
                    </button>
                </>
            )}
        >
            <div className="space-y-4">
                <ErrorBox text={error} />

                <IosSection title="Что закупаем">
                    <Field label="Наименование расхода" required hint="Коротко и конкретно: «Бумага А4 для офиса», а не «хоз. товары». Подробности — в позициях ниже.">
                        <input
                            className={iosInput}
                            value={draft.expense_name}
                            onChange={(event) => set('expense_name', event.target.value)}
                            placeholder="Например: Бумага А4 и канцелярия для офиса"
                            maxLength={300}
                        />
                    </Field>

                    <div className="space-y-1.5">
                        <div className="flex items-center gap-1.5 px-1 text-[11px] font-semibold uppercase tracking-wider text-slate-500">
                            Позиции
                        </div>
                        <div className="overflow-hidden rounded-xl ring-1 ring-slate-200/70">
                            <div className="hidden grid-cols-[1fr_84px_72px_128px_32px] gap-2 bg-slate-50 px-3 py-1.5 text-[11px] uppercase tracking-wider text-slate-500 sm:grid">
                                <span>Товар / услуга</span><span>Кол-во</span><span>Ед.</span><span className="text-right">Цена за ед.</span><span />
                            </div>
                            {draft.items.map((item) => {
                                const remove = () => setDraft((prev) => ({
                                    ...prev,
                                    items: prev.items.length > 1 ? prev.items.filter((row) => row.key !== item.key) : [newItem()],
                                }));
                                return (
                                    <div key={item.key} className="border-t border-slate-200/60 px-3 py-2 sm:grid sm:grid-cols-[1fr_84px_72px_128px_32px] sm:items-center sm:gap-2">
                                        <div className="flex items-center gap-2 sm:contents">
                                            <input
                                                className={`${iosInput} flex-1 py-2 text-[13.5px]`}
                                                placeholder="Наименование"
                                                value={item.name}
                                                onChange={(event) => updateItem(item.key, { name: event.target.value })}
                                                maxLength={300}
                                            />
                                            <button type="button" aria-label="Убрать позицию" className="grid h-8 w-8 shrink-0 place-items-center rounded-full text-slate-400 transition hover:bg-slate-100 hover:text-slate-700 sm:hidden" onClick={remove}>
                                                <X size={14} />
                                            </button>
                                        </div>
                                        <div className="mt-2 grid grid-cols-[84px_72px_1fr] gap-2 sm:contents">
                                            <input
                                                className={`${iosInput} py-2 text-[13.5px] tabular-nums`}
                                                inputMode="decimal"
                                                aria-label="Количество"
                                                value={item.quantity}
                                                onChange={(event) => updateItem(item.key, { quantity: event.target.value })}
                                            />
                                            <input
                                                className={`${iosInput} py-2 text-[13.5px]`}
                                                placeholder="шт"
                                                aria-label="Единица"
                                                value={item.unit}
                                                onChange={(event) => updateItem(item.key, { unit: event.target.value })}
                                                maxLength={32}
                                            />
                                            <AmountInput
                                                ariaLabel="Цена за единицу"
                                                className="py-2 text-right text-[13.5px]"
                                                value={item.unit_price}
                                                onChange={(value) => updateItem(item.key, { unit_price: value })}
                                            />
                                        </div>
                                        <button type="button" aria-label="Убрать позицию" className="hidden h-8 w-8 place-items-center rounded-full text-slate-400 transition hover:bg-slate-100 hover:text-slate-700 sm:grid" onClick={remove}>
                                            <X size={14} />
                                        </button>
                                    </div>
                                );
                            })}
                            <div className="flex items-center justify-between gap-2 border-t border-slate-200/60 bg-slate-50 px-3 py-2">
                                <button type="button" className={`${iosBtnGhost} -ml-2`} onClick={() => setDraft((prev) => ({ ...prev, items: [...prev.items, newItem()] }))}>
                                    <Plus size={14} /> Позиция
                                </button>
                                <div className="text-[13.5px] text-slate-600">
                                    Итого <span className="font-semibold tabular-nums text-slate-900">{fmtMoney(total)}</span>
                                </div>
                            </div>
                        </div>
                    </div>

                    <div className="grid gap-3 sm:grid-cols-2">
                        <Field label="Категория" required>
                            <CustomSelect
                                value={draft.category_id}
                                onChange={(value) => setDraft((prev) => ({ ...prev, category_id: value, subcategory_id: null }))}
                                options={categories.map((item) => ({ value: item.id, label: item.name }))}
                                placeholder={categories.length ? 'Выберите категорию' : 'Справочник пуст — заведите категории'}
                                variant="ios"
                                searchable={categories.length > 8}
                                ariaLabel="Категория"
                            />
                        </Field>
                        <Field label="Подкатегория">
                            <CustomSelect
                                value={draft.subcategory_id}
                                onChange={(value) => set('subcategory_id', value)}
                                options={[{ value: null, label: '—' }, ...subcategories.map((item) => ({ value: item.id, label: item.name }))]}
                                placeholder={draft.category_id ? (subcategories.length ? 'Выберите' : 'У категории нет подкатегорий') : 'Сначала категория'}
                                disabled={!draft.category_id || !subcategories.length}
                                variant="ios"
                                ariaLabel="Подкатегория"
                            />
                        </Field>
                    </div>
                </IosSection>

                <IosSection title="Поставщик и оплата">
                    <Field label="Контрагент" required hint="Поставщик товара или услуги. Нет в списке — впишите название ниже и нажмите «Добавить».">
                        <CustomSelect
                            value={draft.counterparty_id}
                            onChange={(value) => setDraft((prev) => ({ ...prev, counterparty_id: value, contract_id: null }))}
                            options={counterpartyOptions}
                            placeholder="Выберите контрагента"
                            variant="ios"
                            searchable
                            ariaLabel="Контрагент"
                        />
                        <div className="mt-1.5 flex gap-2">
                            <input
                                className={`${iosInput} py-2 text-[13px]`}
                                placeholder="Новый контрагент: название"
                                value={newCounterparty}
                                onChange={(event) => setNewCounterparty(event.target.value)}
                                onKeyDown={(event) => { if (event.key === 'Enter') { event.preventDefault(); createCounterparty(); } }}
                                maxLength={200}
                            />
                            <button type="button" className={`${iosBtnSecondary} shrink-0 py-2`} disabled={!newCounterparty.trim() || creatingCounterparty} onClick={createCounterparty}>
                                {creatingCounterparty ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />} Добавить
                            </button>
                        </div>
                    </Field>

                    {history.length > 0 && (
                        <NoticeBox tone="blue" text="">
                            <div className="text-[11px] font-semibold uppercase tracking-wider text-blue-700/80">История оплат</div>
                            <div className="mt-1 space-y-1">
                                {history.slice(0, 3).map((row) => (
                                    <div key={row.request_id} className="flex flex-wrap items-baseline justify-between gap-x-3 text-[12.5px]">
                                        <span className="min-w-0 flex-1 truncate text-blue-900">
                                            №{row.request_id} · {row.expense_name}{row.counterparty_name ? ` · ${row.counterparty_name}` : ''}
                                        </span>
                                        <span className="tabular-nums text-blue-900/80">
                                            {fmtDate(row.paid_on)} · {fmtMoney(row.paid_amount ?? row.amount)}
                                            {row.unit_price ? ` · ${fmtMoney(row.unit_price)}/ед.` : ''}
                                        </span>
                                    </div>
                                ))}
                            </div>
                        </NoticeBox>
                    )}

                    <div className="grid gap-3 sm:grid-cols-2">
                        <Field label="Источник оплаты" required>
                            <IosSegmented value={draft.payment_source} options={SOURCE_OPTIONS} onChange={(value) => set('payment_source', value)} stretch ariaLabel="Источник оплаты" />
                        </Field>
                        <Field label="Тип оплаты" required>
                            <IosSegmented value={draft.payment_type} options={TYPE_OPTIONS} onChange={(value) => set('payment_type', value)} stretch ariaLabel="Тип оплаты" />
                        </Field>
                    </div>

                    <div className="grid gap-3 sm:grid-cols-2">
                        <Field label="Проект">
                            <CustomSelect value={draft.project_id} onChange={(value) => set('project_id', value)} options={projectOptions} placeholder="Без проекта" variant="ios" ariaLabel="Проект" searchable={projectOptions.length > 8} />
                        </Field>
                        <Field label="Отдел закупа" required optionalMark={false}>
                            <CustomSelect value={draft.department_id} onChange={(value) => set('department_id', value)} options={departmentOptions} placeholder="Отдел" variant="ios" ariaLabel="Отдел" />
                        </Field>
                    </div>

                    {contractNeeded && !extras.includes('contract_id') && (
                        <NoticeBox text={`Сумма выше ${fmtMoney(CONTRACT_THRESHOLD)}: на шаге 7 счёт не уйдёт дальше без действующего договора с контрагентом. Договор можно указать сейчас или позже.`} />
                    )}

                    {extras.map((key) => {
                        const meta = EXTRA_FIELDS.find((field) => field.key === key);
                        return (
                            <div key={key} className="flex items-start gap-2">
                                <div className="min-w-0 flex-1">
                                    {key === 'due_on' && (
                                        <Field label={meta.label} hint="Крайний срок оплаты. Заявка с прошедшим сроком и без оплаты подсвечивается как просроченная.">
                                            <IosDatePicker value={draft.due_on} onChange={(value) => set('due_on', value || '')} allowEmpty placeholder="Выберите дату" triggerClassName={dateTrigger} ariaLabel="Срок оплаты" />
                                        </Field>
                                    )}
                                    {key === 'legal_entity_id' && (
                                        <Field label={meta.label} hint="С какого нашего юр. лица идёт оплата. Обязательно к шагу 7.">
                                            <CustomSelect value={draft.legal_entity_id} onChange={(value) => set('legal_entity_id', value)} options={legalEntityOptions} placeholder={legalEntityOptions.length ? 'Выберите юр. лицо' : 'Справочник пуст'} variant="ios" ariaLabel="Юр. лицо" />
                                        </Field>
                                    )}
                                    {key === 'contract_id' && (
                                        <Field label={meta.label} hint="Договор с этим контрагентом. Для счетов свыше 300 000 ₸ обязателен действующий.">
                                            <CustomSelect value={draft.contract_id} onChange={(value) => set('contract_id', value)} options={contractOptions} placeholder={draft.counterparty_id ? (contractOptions.length ? 'Выберите договор' : 'У контрагента нет договоров в системе') : 'Сначала контрагент'} disabled={!draft.counterparty_id || !contractOptions.length} variant="ios" ariaLabel="Договор" />
                                        </Field>
                                    )}
                                    {key === 'card_number' && (
                                        <Field label={meta.label} hint="Для переводов на банковскую карту получателя. Ищется по цифрам.">
                                            <input className={`${iosInput} tabular-nums`} inputMode="numeric" value={draft.card_number} onChange={(event) => set('card_number', event.target.value.replace(/[^\d ]/g, ''))} placeholder="0000 0000 0000 0000" maxLength={24} />
                                        </Field>
                                    )}
                                    {key === 'notes' && (
                                        <Field label={meta.label} hint="Валюта, номер и дата счёта, юр. лицо, нал/безнал и прочая детализация.">
                                            <textarea className={`${iosInput} min-h-[72px] resize-y`} value={draft.notes} onChange={(event) => set('notes', event.target.value)} maxLength={4000} />
                                        </Field>
                                    )}
                                    {(key === 'branch' || key === 'payment_period') && (
                                        <Field label={meta.label} hint={key === 'payment_period' ? 'За какой период платим: месяц, аванс, постоплата.' : 'Таксопарк (город), филиал или регион — в зависимости от проекта.'}>
                                            <input className={iosInput} value={draft[key]} onChange={(event) => set(key, event.target.value)} maxLength={key === 'branch' ? 200 : 120} placeholder={key === 'payment_period' ? 'Например: сентябрь 2026, аванс' : 'Например: Алматы'} />
                                        </Field>
                                    )}
                                </div>
                                <button type="button" aria-label={`Убрать поле ${meta.label}`} className="mt-6 grid h-8 w-8 shrink-0 place-items-center rounded-full text-slate-400 transition hover:bg-slate-100 hover:text-slate-700" onClick={() => removeExtra(key)}>
                                    <X size={14} />
                                </button>
                            </div>
                        );
                    })}

                    <div className="flex flex-wrap gap-1.5">
                        {EXTRA_FIELDS.filter((field) => !extras.includes(field.key)).map((field) => (
                            <button
                                key={field.key}
                                type="button"
                                onClick={() => addExtra(field.key)}
                                className="inline-flex items-center gap-1 rounded-full bg-slate-100 px-2.5 py-1 text-[12.5px] text-slate-600 transition hover:bg-slate-200 active:scale-[0.98]"
                            >
                                <Plus size={12} /> {field.label}
                            </button>
                        ))}
                    </div>
                </IosSection>

                <IosSection title="Согласующие">
                    <div className="flex items-center justify-between gap-3">
                        <div className="min-w-0">
                            <div className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">Руководитель (шаг 2)</div>
                            {!managerEditing ? (
                                <div className="text-[13.5px] text-slate-900">
                                    {manager ? manager.name : <span className="text-slate-500">не определён — шаг 2 будет пропущен</span>}
                                </div>
                            ) : (
                                <div className="mt-1.5 w-full sm:w-[360px]">
                                    <UserSelect users={users} value={draft.manager_id} onChange={(value) => { set('manager_id', value); setManagerEditing(false); }} exclude={[me?.id]} placeholder="Выберите руководителя" />
                                </div>
                            )}
                        </div>
                        <button type="button" className={`${iosBtnGhost} shrink-0`} onClick={() => setManagerEditing((prev) => !prev)}>
                            {managerEditing ? 'Отмена' : (manager ? 'Изменить' : 'Указать')}
                        </button>
                    </div>
                    <div className="text-[12.5px] text-slate-500">
                        Дальше по маршруту: Учредитель (шаг 3), бухгалтерия (шаги 5, 8, 10, 12) и снова Учредитель или согласующий по Приказу (шаг 9).
                    </div>
                </IosSection>

                {!editing && (
                    <IosSection title="Документы к закупу" hint="Реестр поставщиков со сравнением цен и ссылками на товар либо КП, если альтернатив нет. Без файла шаг 1 не пройти.">
                        <FilePicker files={files} onChange={setFiles} kinds={kinds} />
                    </IosSection>
                )}
            </div>
        </IosModal>
    );
};

export default PaymentRequestForm;
