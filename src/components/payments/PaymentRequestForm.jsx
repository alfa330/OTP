import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import { Loader2, Plus, X } from 'lucide-react';
import { iosBtnGhost, iosBtnPrimary, iosBtnSecondary, iosInput, IosModal, IosSection } from '../ui/ios';
import CustomSelect from '../ui/CustomSelect';
import IosDatePicker from '../ui/DatePicker';
import {
    ATTACHMENT_LABELS, CONTRACT_THRESHOLD, DEFAULT_UNIT, HINTS, SOURCE_OPTIONS, SUPPLIER_KIND_OPTIONS, TYPE_OPTIONS,
    VAT_OPTIONS, fmtDate, fmtMoney, isBlankItem, itemProblem, itemQuantity, itemTotal, itemsTotal, parseAmount,
    priceTrend, trendLabel, unitOptions,
} from './paymentsMeta';
import {
    AmountInput, Choice, ErrorBox, Field, FieldHint, FilePicker, NoticeBox, QuantityInput, UserSelect,
    appendPayloadFiles, errorText,
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
 *
 * Форма рассчитана на человека, который видит её впервые (решение владельца
 * 06.10.2026): у каждого выбора под «i» — зачем поле и что значит каждый
 * вариант (тексты — HINTS в paymentsMeta.js); позиции устроены как таблица в
 * счёте поставщика — количество, единица ИЗ СПИСКА, цена за единицу и сумма
 * строки, чтобы было видно, из чего складывается итог.
 */

// Сетка строки позиции на широком экране. До 768 px мобильная оболочка сайта
// схлопывает произвольные сетки в одну колонку, поэтому там строка собрана на
// flex, а сетка включается только с md.
const ITEM_GRID = 'md:grid md:grid-cols-[minmax(0,1fr)_72px_100px_124px_112px_28px] md:items-center md:gap-2';
/* На телефоне шапки таблицы нет — подпись стоит над каждым полем строки: без неё
   «20 · шт · 2 500» — три числа без объяснения, что из них количество, а что цена. */
const ITEM_CAPTION = 'mb-1 block px-1 text-[10.5px] font-medium uppercase tracking-wider text-slate-400 md:hidden';

const EXTRA_FIELDS = [
    { key: 'branch', label: 'Филиал / регион' },
    { key: 'payment_period', label: 'Период оплаты' },
    { key: 'due_on', label: 'Срок оплаты' },
    { key: 'legal_entity_id', label: 'Юр. лицо' },
    { key: 'contract_id', label: 'Договор' },
    { key: 'card_number', label: 'Номер карты' },
    { key: 'notes', label: 'Примечания' },
];

const newItem = () => ({ key: Math.random().toString(36).slice(2, 9), name: '', quantity: '1', unit: DEFAULT_UNIT, unit_price: 0 });

// Количество из базы («5.000») — в том виде, как его набирает человек («5», «2,5»).
const quantityText = (value) => {
    const number = parseAmount(value);
    return number ? String(number).replace('.', ',') : '1';
};

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
    supplier_kind: null,
    supplier_vat: null,
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
    supplier_kind: request.supplier_kind ?? null,
    supplier_vat: request.supplier_vat ?? null,
    items: (items && items.length ? items : [{}]).map((item) => ({
        key: Math.random().toString(36).slice(2, 9),
        name: item.name || '',
        quantity: quantityText(item.quantity),
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
    stepFiles = [], canAddCounterparty = false,
}) => {
    const editing = Boolean(request);
    const [draft, setDraft] = useState(() => emptyDraft(me, dictionaries?.manager));
    const [extras, setExtras] = useState([]);
    const [files, setFiles] = useState([]);
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState('');
    const errorRef = useRef(null);
    const [managerEditing, setManagerEditing] = useState(false);
    const [history, setHistory] = useState([]);
    const [newCounterparty, setNewCounterparty] = useState('');
    const [creatingCounterparty, setCreatingCounterparty] = useState(false);
    const [addingCounterparty, setAddingCounterparty] = useState(false);
    const [localDicts, setLocalDicts] = useState(dictionaries);

    useEffect(() => { setLocalDicts(dictionaries); }, [dictionaries]);

    const toastRef = useRef(showToast);
    useEffect(() => { toastRef.current = showToast; }, [showToast]);

    // Кнопка отправки — в подвале, а ошибка — над формой: без прокрутки
    // человек жмёт «Создать», и ничего видимого не происходит.
    useEffect(() => {
        if (error) errorRef.current?.scrollIntoView({ block: 'start', behavior: 'smooth' });
    }, [error]);

    useEffect(() => {
        if (!open) return;
        setError('');
        setFiles([]);
        setManagerEditing(false);
        setNewCounterparty('');
        setAddingCounterparty(false);
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
    const trend = useMemo(() => priceTrend(history), [history]);

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
            // Такой контрагент уже есть (сервер отвечает 409 с его id) — просто выбираем его.
            const response = await axios.post(`${apiBaseUrl}/api/payments/dictionaries/counterparties`, { name }, {
                headers: headers(),
                validateStatus: (status) => (status >= 200 && status < 300) || status === 409,
            });
            const id = response.data?.id;
            if (!id) throw new Error(response.data?.error || 'Не удалось добавить контрагента');
            const refreshed = await axios.get(`${apiBaseUrl}/api/payments/dictionaries`, { headers: headers() });
            setLocalDicts(refreshed.data);
            set('counterparty_id', id);
            setNewCounterparty('');
            setAddingCounterparty(false);
        } catch (err) {
            toastRef.current?.(errorText(err, 'Не удалось добавить контрагента'), 'error');
        } finally {
            setCreatingCounterparty(false);
        }
    };

    /* Форму поставщика и НДС инициатор называет на шаге 4. В форме заявки они
       есть, только чтобы поправить уже выбранное: при создании лишний вопрос
       не нужен. */
    const supplierEditable = Boolean(editing && (draft.supplier_kind || draft.supplier_vat === true || draft.supplier_vat === false));

    const validate = () => {
        if (!draft.expense_name.trim()) return 'Укажите наименование расхода';
        const filled = draft.items.filter((item) => !isBlankItem(item));
        if (!filled.length) return 'Добавьте хотя бы одну позицию: что покупаем, сколько и по какой цене';
        const problem = filled.map(itemProblem).find(Boolean);
        if (problem) return problem;
        if (total <= 0) return 'Сумма заявки должна быть больше нуля — укажите цену за единицу';
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
        // Форма поставщика и НДС уходят, только если их здесь правили: иначе форма,
        // открытая до шага 4, при сохранении затёрла бы выбранное на шаге пустым.
        const { supplier_kind: supplierKind, supplier_vat: supplierVat, ...rest } = draft;
        const payload = {
            ...rest,
            ...(supplierEditable ? { supplier_kind: supplierKind, supplier_vat: supplierVat } : {}),
            // Те же строки, что посчитаны в «Итого» на экране: пустые не уходят,
            // а строка без названия или количества до сюда не доходит (validate).
            items: draft.items.filter((item) => !isBlankItem(item)).map((item) => ({
                name: item.name.trim(), quantity: itemQuantity(item), unit: item.unit || null,
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

    const kinds = useMemo(() => {
        const list = stepFiles.length ? stepFiles : ['supplier_registry', 'offer'];
        return (list.includes('other') ? list : [...list, 'other'])
            .map((value) => ({ value, label: ATTACHMENT_LABELS[value] || value }));
    }, [stepFiles]);

    return (
        <IosModal
            open={open}
            onClose={onClose}
            title={editing ? `Заявка №${request.id} — правка` : 'Новая заявка на закуп'}
            subtitle={editing ? request.expense_name : 'Шаг 1 из 12 · Согласование закупки'}
            maxWidth="max-w-3xl"
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
                <div ref={errorRef} className="scroll-mt-4"><ErrorBox text={error} /></div>

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

                    <Field as="div" label="Позиции" required optionalMark={false} hint={HINTS.items}>
                        <div className="overflow-hidden rounded-xl ring-1 ring-slate-200/70">
                            <div className={`hidden ${ITEM_GRID} bg-slate-50 px-3 py-1.5 text-[11px] uppercase tracking-wider text-slate-500`}>
                                <span>Товар или услуга</span>
                                <span>Кол-во</span>
                                <span>Ед. изм.</span>
                                <span className="text-right">Цена за ед., ₸</span>
                                <span className="text-right">Сумма, ₸</span>
                                <span />
                            </div>
                            {draft.items.map((item) => {
                                const remove = () => setDraft((prev) => ({
                                    ...prev,
                                    items: prev.items.length > 1 ? prev.items.filter((row) => row.key !== item.key) : [newItem()],
                                }));
                                const blank = isBlankItem(item);
                                const nameMissing = !blank && !item.name.trim();
                                const quantityBad = !blank && !(itemQuantity(item) > 0);
                                return (
                                    <div key={item.key} className={`border-t border-slate-200/60 px-3 py-2 ${ITEM_GRID}`}>
                                        <div className="flex items-center gap-2 md:contents">
                                            <input
                                                className={`${iosInput} min-w-0 flex-1 py-2 text-[13.5px] ${nameMissing ? 'ring-2 ring-rose-300' : ''}`}
                                                placeholder="Например: Бумага А4, 500 листов"
                                                aria-label="Товар или услуга"
                                                aria-invalid={nameMissing || undefined}
                                                value={item.name}
                                                onChange={(event) => updateItem(item.key, { name: event.target.value })}
                                                maxLength={300}
                                            />
                                            <button type="button" aria-label="Убрать позицию" className="grid h-8 w-8 shrink-0 place-items-center rounded-full text-slate-400 transition hover:bg-slate-100 hover:text-slate-700 md:hidden" onClick={remove}>
                                                <X size={14} />
                                            </button>
                                        </div>
                                        <div className="mt-2 flex gap-2 md:contents">
                                            <div className="w-[72px] shrink-0 md:w-auto">
                                                <span className={ITEM_CAPTION}>Кол-во</span>
                                                <QuantityInput
                                                    className="py-2 text-[13.5px]"
                                                    value={item.quantity}
                                                    invalid={quantityBad}
                                                    onChange={(value) => updateItem(item.key, { quantity: value })}
                                                />
                                            </div>
                                            <div className="w-[100px] shrink-0 md:w-auto">
                                                <span className={ITEM_CAPTION}>Ед. изм.</span>
                                                <CustomSelect
                                                    value={item.unit || null}
                                                    onChange={(value) => updateItem(item.key, { unit: value || '' })}
                                                    options={unitOptions(item.unit)}
                                                    placeholder="ед."
                                                    variant="ios"
                                                    textClassName="text-[13.5px] text-slate-900"
                                                    ariaLabel="Единица измерения"
                                                />
                                            </div>
                                            <div className="min-w-0 flex-1 md:flex-none">
                                                <span className={`${ITEM_CAPTION} text-right`}>Цена за ед., ₸</span>
                                                <AmountInput
                                                    ariaLabel="Цена за единицу"
                                                    className="py-2 text-right text-[13.5px]"
                                                    value={item.unit_price}
                                                    onChange={(value) => updateItem(item.key, { unit_price: value })}
                                                />
                                            </div>
                                        </div>
                                        {/* Сумма строки — чтобы формула была на виду: количество × цена. */}
                                        <div className="mt-1.5 flex items-baseline justify-between gap-2 text-[12.5px] text-slate-500 md:mt-0 md:block md:text-right">
                                            <span className="md:hidden">Сумма: количество × цена</span>
                                            {/* На компьютере «₸» стоит в шапке колонки, на телефоне шапки нет — знак у числа. */}
                                            <span className="text-[13.5px] tabular-nums text-slate-900">
                                                {blank ? '—' : fmtMoney(itemTotal(item), { currency: false })}
                                                {!blank && <span className="md:hidden">{'\u00a0₸'}</span>}
                                            </span>
                                        </div>
                                        <button type="button" aria-label="Убрать позицию" className="hidden h-7 w-7 place-items-center rounded-full text-slate-400 transition hover:bg-slate-100 hover:text-slate-700 md:grid" onClick={remove}>
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
                    </Field>

                    <div className="grid gap-3 sm:grid-cols-2">
                        <Field label="Категория" required hint={HINTS.category}>
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
                        {/* У категории без подкатегорий поля нет вовсе: заблокированный
                            список, в котором нечего выбрать, только отвлекает. */}
                        {(!draft.category_id || subcategories.length > 0) && (
                            <Field label="Подкатегория" hint={HINTS.subcategory}>
                                <CustomSelect
                                    value={draft.subcategory_id}
                                    onChange={(value) => set('subcategory_id', value)}
                                    options={[{ value: null, label: 'Без подкатегории' }, ...subcategories.map((item) => ({ value: item.id, label: item.name }))]}
                                    placeholder={draft.category_id ? 'Без подкатегории' : 'Сначала выберите категорию'}
                                    disabled={!draft.category_id}
                                    variant="ios"
                                    ariaLabel="Подкатегория"
                                />
                            </Field>
                        )}
                    </div>
                </IosSection>

                <IosSection title="Поставщик и оплата">
                    <Field label="Контрагент" required hint="Поставщик товара или услуги. Нет в списке — «Новый контрагент»: достаточно названия, БИН и реквизиты бухгалтерия допишет в справочнике.">
                        <CustomSelect
                            value={draft.counterparty_id}
                            onChange={(value) => setDraft((prev) => ({ ...prev, counterparty_id: value, contract_id: null }))}
                            options={counterpartyOptions}
                            placeholder="Выберите контрагента"
                            variant="ios"
                            searchable
                            ariaLabel="Контрагент"
                        />
                    </Field>
                    {/* Новый контрагент — по запросу, а не полем на виду: в большинстве
                        заявок поставщик уже есть в справочнике. */}
                    {canAddCounterparty && (addingCounterparty ? (
                        <div className="-mt-1 flex gap-2">
                            <input
                                autoFocus
                                className={`${iosInput} py-2 text-[13px]`}
                                placeholder="Название нового контрагента"
                                value={newCounterparty}
                                onChange={(event) => setNewCounterparty(event.target.value)}
                                onKeyDown={(event) => {
                                    if (event.key === 'Enter') { event.preventDefault(); createCounterparty(); }
                                    if (event.key === 'Escape') { event.stopPropagation(); setAddingCounterparty(false); setNewCounterparty(''); }
                                }}
                                maxLength={200}
                            />
                            <button type="button" className={`${iosBtnPrimary} shrink-0 py-2`} disabled={!newCounterparty.trim() || creatingCounterparty} onClick={createCounterparty}>
                                {creatingCounterparty ? <Loader2 size={14} className="animate-spin" /> : null} Добавить
                            </button>
                            <button type="button" className={`${iosBtnGhost} shrink-0 py-2`} onClick={() => { setAddingCounterparty(false); setNewCounterparty(''); }}>Отмена</button>
                        </div>
                    ) : (
                        <button type="button" className="-mt-1.5 inline-flex items-center gap-1 px-1 text-[12.5px] font-medium text-blue-600 transition hover:underline" onClick={() => setAddingCounterparty(true)}>
                            <Plus size={13} /> Новый контрагент
                        </button>
                    ))}

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
                                            {trend[row.request_id] ? <span className="ml-1 font-medium text-blue-900">{trendLabel(trend[row.request_id])}</span> : null}
                                        </span>
                                    </div>
                                ))}
                            </div>
                        </NoticeBox>
                    )}

                    {/* Сегменты — рядом, пока помещаются, иначе друг под другом:
                        «Ежемесячный» и «Фиксированный» в половине ширины окна вылезали
                        за карточку. */}
                    <div className="flex flex-wrap gap-x-6 gap-y-3">
                        <Field as="div" label="Источник оплаты" required hint={HINTS.source}>
                            <Choice value={draft.payment_source} options={SOURCE_OPTIONS} onChange={(value) => set('payment_source', value)} ariaLabel="Источник оплаты" />
                        </Field>
                        <Field as="div" label="Тип оплаты" required hint={HINTS.type}>
                            <Choice value={draft.payment_type} options={TYPE_OPTIONS} onChange={(value) => set('payment_type', value)} ariaLabel="Тип оплаты" />
                        </Field>
                    </div>

                    {supplierEditable && (
                        <div className="flex flex-wrap gap-x-6 gap-y-3">
                            <Field as="div" label="Поставщик" optionalMark={false} hint={HINTS.supplierKind}>
                                <Choice value={draft.supplier_kind} options={SUPPLIER_KIND_OPTIONS} onChange={(value) => set('supplier_kind', value)} ariaLabel="Форма поставщика" />
                            </Field>
                            <Field as="div" label="НДС" optionalMark={false} hint={HINTS.supplierVat}>
                                <Choice value={draft.supplier_vat} options={VAT_OPTIONS} onChange={(value) => set('supplier_vat', value)} ariaLabel="НДС поставщика" />
                            </Field>
                        </div>
                    )}

                    <div className="grid gap-3 sm:grid-cols-2">
                        <Field label="Проект" hint={HINTS.project}>
                            <CustomSelect value={draft.project_id} onChange={(value) => set('project_id', value)} options={projectOptions} placeholder="Без проекта" variant="ios" ariaLabel="Проект" searchable={projectOptions.length > 8} />
                        </Field>
                        <Field label="Отдел закупа" required optionalMark={false} hint={HINTS.department}>
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
                                        <Field label={meta.label} hint={{ ...HINTS.legalEntity, outro: 'Можно не указывать сейчас: бухгалтерия назовёт его на шаге 5.' }}>
                                            <CustomSelect value={draft.legal_entity_id} onChange={(value) => set('legal_entity_id', value)} options={legalEntityOptions} placeholder={legalEntityOptions.length ? 'Выберите юр. лицо' : 'Справочник пуст'} variant="ios" ariaLabel="Юр. лицо" />
                                        </Field>
                                    )}
                                    {key === 'contract_id' && (
                                        <Field label={meta.label} hint={HINTS.contract}>
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
                            <div className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-slate-500">
                                Руководитель (шаг 2)
                                <FieldHint hint={HINTS.manager} />
                            </div>
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
                    <IosSection title={<span className="inline-flex items-center gap-1.5">Документы к закупу <FieldHint hint={HINTS.documents} /></span>}>
                        <FilePicker files={files} onChange={setFiles} kinds={kinds} />
                    </IosSection>
                )}
            </div>
        </IosModal>
    );
};

export default PaymentRequestForm;
