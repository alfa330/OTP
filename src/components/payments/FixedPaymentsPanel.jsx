import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import { CalendarClock, Download, Loader2, Pencil, Play, Plus, Trash2, Upload } from 'lucide-react';
import { iosBtnGhost, iosBtnPrimary, iosBtnSecondary, iosCard, iosInput, IosMenu, IosModal } from '../ui/ios';
import IosDatePicker from '../ui/DatePicker';
import {
    ACCOUNTING_CATEGORY_OPTIONS, AUTO_CREATE_OPTIONS, HINTS, OBJECT_TYPE_OPTIONS, PERIOD_META, PERIOD_OPTIONS, fmtDate,
    fmtMoney,
} from './paymentsMeta';
import { AmountInput, Choice, DATE_TRIGGER, ErrorBox, Field, FormSelect, NoticeBox, UserSelect, errorText } from './paymentsUi';

const TEMPLATE_ACTIVE_OPTIONS = [{ value: true, label: 'Активен' }, { value: false, label: 'Приостановлен' }];

/*
 * Справочник «Регулярные платежи» (ТЗ «Закуп и оплата», пп. 4.4 и 13): аренда,
 * связь, интернет, облачные сервисы, лицензии — обязательства, у которых
 * поставщик и условия уже утверждены.
 *
 * Запись хранит всё, что заявка подставляет сама: поставщика, договор,
 * компанию-плательщика, банковские реквизиты, назначение платежа, лимит,
 * периодичность и согласующего. Инициатору остаётся период, сумма и счёт.
 *
 * Заявку по платежу заводит человек («Создать заявку» → «Регулярный платёж»)
 * либо сам раздел в начале периода — это выбирается у каждого платежа. Здесь же
 * импорт перечня из Excel с предпросмотром.
 *
 * Живёт внутри «Справочников» (п. 13 называет регулярные платежи справочником):
 * заголовок и пояснение рисует их общий экран, здесь — строка действий и список.
 */

const emptyDraft = (me) => ({
    id: null, name: '', amount: 0, periodicity: 'monthly', interval_days: '', next_due_on: '', lead_days: 7,
    project_id: null, branch: '', responsible_user_id: me?.id || null, category_id: null, subcategory_id: null,
    counterparty_id: null, legal_entity_id: null, contract_id: null, counterparty_account_id: null,
    payment_purpose: '', amount_limit: '', approver_user_id: null, object_type: 'service', accounting_category: null,
    auto_create: true, note: '', is_active: true,
});

const FixedPaymentsPanel = ({
    apiBaseUrl, headers, users, dictionaries, me, showToast, canEdit, onChanged, onRequestsChanged, onOpenRequest,
}) => {
    const [rows, setRows] = useState([]);
    const [loading, setLoading] = useState(false);
    const [editing, setEditing] = useState(false);
    const [draft, setDraft] = useState(() => emptyDraft(me));
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState('');
    const [running, setRunning] = useState(false);
    const [confirmDelete, setConfirmDelete] = useState(false);
    // Платёж, по которому заявка создаётся прямо сейчас: второй щелчок до ответа
    // завёл бы вторую заявку — уже на следующий период.
    const [generating, setGenerating] = useState(null);
    const [importOpen, setImportOpen] = useState(false);
    const [importRows, setImportRows] = useState(null);
    const [importing, setImporting] = useState(false);
    const [importError, setImportError] = useState('');
    const fileRef = useRef(null);

    const toastRef = useRef(showToast);
    useEffect(() => { toastRef.current = showToast; }, [showToast]);

    const load = useCallback(async () => {
        setLoading(true);
        try {
            const response = await axios.get(`${apiBaseUrl}/api/payments/templates`, { headers: headers() });
            setRows(response.data?.items || []);
        } catch (err) {
            toastRef.current?.(errorText(err, 'Не удалось загрузить регулярные платежи'), 'error');
        } finally {
            setLoading(false);
        }
    }, [apiBaseUrl, headers]);

    useEffect(() => { load(); }, [load]);

    const set = (key, value) => setDraft((prev) => ({ ...prev, [key]: value }));

    const categories = useMemo(() => (dictionaries?.categories || []).filter((item) => !item.parent_id), [dictionaries]);
    const subcategories = useMemo(() => (dictionaries?.categories || []).filter((item) => item.parent_id === draft.category_id), [dictionaries, draft.category_id]);
    const projectOptions = useMemo(() => [{ value: '', label: 'Без проекта' }, ...(dictionaries?.projects || []).map((item) => ({ value: item.id, label: item.name }))], [dictionaries]);
    const counterpartyOptions = useMemo(() => (dictionaries?.counterparties || []).map((item) => ({ value: item.id, label: item.name })), [dictionaries]);
    const legalEntityOptions = useMemo(() => (dictionaries?.legal_entities || []).map((item) => ({ value: item.id, label: item.name })), [dictionaries]);
    // Договор и реквизиты — только выбранного поставщика: чужие к платежу не относятся.
    const contractOptions = useMemo(() => [{ value: '', label: 'Без договора' }, ...(dictionaries?.contracts || [])
        .filter((item) => item.counterparty_id === draft.counterparty_id && (item.status === 'active' || item.id === draft.contract_id))
        .map((item) => ({ value: item.id, label: `№${item.number}${item.ends_on ? ` до ${fmtDate(item.ends_on)}` : ''}` }))], [dictionaries, draft.counterparty_id, draft.contract_id]);
    const accountOptions = useMemo(() => [{ value: '', label: 'Не выбраны' }, ...(dictionaries?.counterparty_accounts || [])
        .filter((item) => item.counterparty_id === draft.counterparty_id)
        .map((item) => ({ value: item.id, label: item.text || item.iik }))], [dictionaries, draft.counterparty_id]);

    const openEditor = (row) => {
        setError('');
        setConfirmDelete(false);
        setDraft(row ? {
            id: row.id, name: row.name || '', amount: Number(row.amount) || 0, periodicity: row.periodicity || 'monthly',
            interval_days: row.interval_days || '', next_due_on: row.next_due_on ? String(row.next_due_on).slice(0, 10) : '',
            lead_days: row.lead_days ?? 7, project_id: row.project_id ?? null, branch: row.branch || '',
            responsible_user_id: row.responsible_user_id ?? null, category_id: row.category_id ?? null,
            subcategory_id: row.subcategory_id ?? null, counterparty_id: row.counterparty_id ?? null,
            legal_entity_id: row.legal_entity_id ?? null, contract_id: row.contract_id ?? null,
            counterparty_account_id: row.counterparty_account_id ?? null, payment_purpose: row.payment_purpose || '',
            amount_limit: row.amount_limit ?? '', approver_user_id: row.approver_user_id ?? null,
            object_type: row.object_type || 'service', accounting_category: row.accounting_category ?? null,
            auto_create: row.auto_create !== false, note: row.note || '', is_active: row.is_active ?? true,
        } : emptyDraft(me));
        setEditing(true);
    };

    const save = async () => {
        setSaving(true);
        setError('');
        try {
            const payload = { ...draft, amount_limit: draft.amount_limit === '' || !draft.amount_limit ? null : draft.amount_limit };
            await axios.post(`${apiBaseUrl}/api/payments/templates`, payload, { headers: headers() });
            toastRef.current?.('Платёж сохранён', 'success');
            setEditing(false);
            load();
            onChanged?.();
        } catch (err) {
            setError(errorText(err, 'Не удалось сохранить'));
        } finally {
            setSaving(false);
        }
    };

    // Удаление — из окна записи и с подтверждением: в меню строки оно стояло рядом с
    // «Создать заявку сейчас» и срабатывало с одного промаха. Платёж, по которому
    // уже есть заявки, сервер удалить не даст — его приостанавливают.
    const remove = async () => {
        setSaving(true);
        try {
            await axios.delete(`${apiBaseUrl}/api/payments/templates/${draft.id}`, { headers: headers() });
            toastRef.current?.('Платёж удалён', 'success');
            setEditing(false);
            load();
            onChanged?.();
        } catch (err) {
            setError(errorText(err, 'Не удалось удалить'));
        } finally {
            setSaving(false);
            setConfirmDelete(false);
        }
    };

    const generateNow = async (row) => {
        if (generating) return;
        setGenerating(row.id);
        try {
            const response = await axios.post(`${apiBaseUrl}/api/payments/templates/${row.id}/generate`, {}, { headers: headers() });
            toastRef.current?.(`Заявка №${response.data?.request_id} создана`, 'success');
            load();
            onRequestsChanged?.();
            if (response.data?.request_id) onOpenRequest?.(response.data.request_id);
        } catch (err) {
            toastRef.current?.(errorText(err, 'Не удалось создать заявку'), 'error');
        } finally {
            setGenerating(null);
        }
    };

    const runDue = async () => {
        setRunning(true);
        try {
            const response = await axios.post(`${apiBaseUrl}/api/payments/templates/generate`, {}, { headers: headers() });
            const count = (response.data?.created || []).length;
            toastRef.current?.(count ? `Создано заявок: ${count}` : 'Сегодня заявок по регулярным платежам не требуется', count ? 'success' : 'info');
            load();
            onRequestsChanged?.();
        } catch (err) {
            toastRef.current?.(errorText(err, 'Не удалось создать заявки'), 'error');
        } finally {
            setRunning(false);
        }
    };

    const pickFile = async (event) => {
        const file = event.target.files?.[0];
        event.target.value = '';
        if (!file) return;
        setImporting(true);
        setImportError('');
        try {
            const form = new FormData();
            form.append('file', file, file.name);
            const response = await axios.post(`${apiBaseUrl}/api/payments/templates/import`, form, { headers: headers() });
            setImportRows(response.data?.rows || []);
            setImportOpen(true);
        } catch (err) {
            setImportError(errorText(err, 'Не удалось прочитать файл'));
            setImportRows([]);
            setImportOpen(true);
        } finally {
            setImporting(false);
        }
    };

    const confirmImport = async () => {
        setImporting(true);
        setImportError('');
        try {
            const response = await axios.post(`${apiBaseUrl}/api/payments/templates/import/confirm`, { rows: importRows }, { headers: headers() });
            const { created = 0, existing = 0, skipped = 0 } = response.data || {};
            toastRef.current?.([`Загружено платежей: ${created}`, existing && `уже были в справочнике: ${existing}`,
                skipped && `с ошибками: ${skipped}`].filter(Boolean).join(', '), 'success');
            setImportOpen(false);
            setImportRows(null);
            load();
            onChanged?.();
        } catch (err) {
            setImportError(errorText(err, 'Не удалось загрузить'));
        } finally {
            setImporting(false);
        }
    };

    const downloadSample = async () => {
        try {
            const response = await axios.get(`${apiBaseUrl}/api/payments/templates/sample`, { headers: headers(), responseType: 'blob' });
            const url = URL.createObjectURL(response.data);
            const link = document.createElement('a');
            link.href = url;
            link.download = 'Регулярные платежи.xlsx';
            document.body.appendChild(link);
            link.click();
            link.remove();
            setTimeout(() => URL.revokeObjectURL(url), 1000);
        } catch (err) {
            toastRef.current?.(errorText(err, 'Не удалось скачать образец'), 'error');
        }
    };

    const dueCount = rows.filter((row) => row.is_due).length;
    const importReady = (importRows || []).filter((row) => !row.errors?.length && !row.exists).length;
    const dueLine = (row) => (
        <>
            срок {fmtDate(row.next_due_on)}{row.is_due ? <span className="ml-1 text-amber-700">· пора создать</span> : (row.auto_create !== false && row.generate_on ? ` · заявка ${fmtDate(row.generate_on)}` : '')}
        </>
    );

    return (
        <div className="space-y-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="text-[13px] font-medium text-slate-700">{dueCount ? `Пора создать заявок: ${dueCount}` : ''}</div>
                {canEdit && (
                    <div className="flex flex-wrap items-center gap-2">
                        <input ref={fileRef} type="file" accept=".xlsx" className="hidden" onChange={pickFile} />
                        <button type="button" className={iosBtnGhost} onClick={downloadSample}><Download size={14} /> Образец файла</button>
                        <button type="button" className={iosBtnSecondary} disabled={importing} onClick={() => fileRef.current?.click()}>
                            {importing ? <Loader2 size={14} className="animate-spin" /> : <Upload size={14} />} Импорт из Excel
                        </button>
                        <button type="button" className={iosBtnSecondary} disabled={running || !dueCount} onClick={runDue} title={dueCount ? 'Создать заявки, у которых наступило начало периода' : 'Сегодня создавать нечего'}>
                            {running ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />} Создать заявки за сегодня
                        </button>
                        <button type="button" className={iosBtnPrimary} onClick={() => openEditor(null)}><Plus size={15} /> Платёж</button>
                    </div>
                )}
            </div>

            <div className={`${iosCard} overflow-hidden`}>
                {loading && !rows.length && (
                    <div className="flex items-center justify-center gap-2 py-10 text-[13px] text-slate-500"><Loader2 size={15} className="animate-spin" /> Загружаем…</div>
                )}
                {!loading && !rows.length && (
                    <div className="flex flex-col items-center gap-2 px-4 py-10 text-center text-[13.5px] text-slate-500">
                        <CalendarClock size={22} className="text-slate-300" />
                        Регулярных платежей пока нет.{canEdit ? ' Добавьте платёж или загрузите перечень из Excel.' : ''}
                    </div>
                )}
                {rows.map((row, index) => (
                    <div key={row.id} data-template={row.id} className={`flex items-center gap-3 px-4 py-2.5 ${index > 0 ? 'border-t border-slate-100' : ''} ${row.is_active ? '' : 'opacity-60'}`}>
                        <div className="min-w-0 grow basis-0">
                            <div className="flex flex-wrap items-baseline gap-x-2">
                                <span className="truncate text-[13.5px] font-medium text-slate-900">{row.name}</span>
                                <span className="text-[12.5px] text-slate-500">
                                    {PERIOD_META[row.periodicity]?.label || row.periodicity}{row.periodicity === 'custom' && row.interval_days ? ` (${row.interval_days} дн.)` : ''}
                                    {row.is_active ? '' : ' · приостановлен'}
                                </span>
                            </div>
                            <div className="truncate text-[12px] text-slate-500">
                                {[row.counterparty_name, row.contract_number && `договор №${row.contract_number}`, row.legal_entity_name && `от ${row.legal_entity_name}`,
                                    row.responsible_name && `отв. ${row.responsible_name}`, row.approver_name && `согласует ${row.approver_name}`,
                                    row.auto_create === false && 'заявка — вручную'].filter(Boolean).join(' · ') || '—'}
                            </div>
                            {/* На телефоне правой колонки нет — сумма, срок и открытая заявка идут строкой под названием. */}
                            <div className="mt-0.5 flex flex-wrap items-baseline gap-x-2 text-[12px] tabular-nums text-slate-500 sm:hidden">
                                <span className="text-[13px] text-slate-900">{fmtMoney(row.amount)}</span>
                                <span>{dueLine(row)}</span>
                                {row.open_request_id && (
                                    <button type="button" className="text-blue-600" onClick={() => onOpenRequest?.(row.open_request_id)}>заявка №{row.open_request_id}</button>
                                )}
                            </div>
                        </div>
                        <div className="hidden text-right sm:block">
                            <div className="text-[13.5px] tabular-nums text-slate-900">{fmtMoney(row.amount)}</div>
                            <div className="text-[12px] tabular-nums text-slate-500">{dueLine(row)}</div>
                        </div>
                        {row.open_request_id && (
                            <button type="button" className="hidden shrink-0 text-[12.5px] text-blue-600 hover:underline sm:block" onClick={() => onOpenRequest?.(row.open_request_id)}>
                                заявка №{row.open_request_id}
                            </button>
                        )}
                        {canEdit && (generating === row.id ? <Loader2 size={15} className="mx-2 shrink-0 animate-spin text-slate-400" /> : (
                            <IosMenu
                                label="Действия с платежом"
                                items={[
                                    { key: 'edit', label: 'Изменить', icon: Pencil, onSelect: () => openEditor(row) },
                                    { key: 'now', label: 'Создать заявку сейчас', icon: Play, onSelect: () => generateNow(row), hint: fmtDate(row.next_due_on) },
                                ]}
                            />
                        ))}
                    </div>
                ))}
            </div>

            <IosModal
                open={editing}
                onClose={() => setEditing(false)}
                title={draft.id ? 'Регулярный платёж: правка' : 'Новый регулярный платёж'}
                maxWidth="max-w-2xl"
                footer={(
                    <>
                        {draft.id && (confirmDelete ? (
                            <div className="mr-auto flex items-center gap-2 text-[12.5px] text-rose-700">
                                Удалить без возврата?
                                <button type="button" className={`${iosBtnSecondary} !text-rose-700`} disabled={saving} onClick={remove}>Да, удалить</button>
                                <button type="button" className={`${iosBtnSecondary}`} onClick={() => setConfirmDelete(false)}>Нет</button>
                            </div>
                        ) : (
                            <button type="button" className={`${iosBtnSecondary} mr-auto !text-rose-600`} disabled={saving} onClick={() => setConfirmDelete(true)}>
                                <Trash2 size={14} /> Удалить
                            </button>
                        ))}
                        <button type="button" className={iosBtnSecondary} onClick={() => setEditing(false)} disabled={saving}>Отмена</button>
                        <button type="button" className={iosBtnPrimary} onClick={save} disabled={saving}>{saving && <Loader2 size={14} className="animate-spin" />} Сохранить</button>
                    </>
                )}
            >
                <div className="space-y-3">
                    <ErrorBox text={error} />
                    <Field label="Наименование" required optionalMark={false}><input className={iosInput} value={draft.name} onChange={(e) => set('name', e.target.value)} maxLength={300} placeholder="Аренда офиса Алматы" /></Field>
                    <div className="grid gap-3 sm:grid-cols-2">
                        <Field label="Сумма, ₸" required optionalMark={false} hint="Обычная сумма платежа. В заявке её можно изменить под счёт периода."><AmountInput value={draft.amount} onChange={(v) => set('amount', v)} ariaLabel="Сумма" /></Field>
                        <Field label="Периодичность" required optionalMark={false} hint={HINTS.periodicity}>
                            <FormSelect value={draft.periodicity} onChange={(v) => set('periodicity', v)} options={PERIOD_OPTIONS} ariaLabel="Периодичность" />
                        </Field>
                    </div>
                    <div className="grid gap-3 sm:grid-cols-2">
                        <Field label="Ближайшая дата оплаты" required optionalMark={false} hint="Срок ближайшего платежа. После создания заявки срок сам сдвигается на период вперёд.">
                            <IosDatePicker value={draft.next_due_on} onChange={(v) => set('next_due_on', v || '')} allowEmpty placeholder="Дата" triggerClassName={DATE_TRIGGER} ariaLabel="Ближайшая дата оплаты" />
                        </Field>
                        <Field label="Ответственный" required optionalMark={false} hint="От его имени раздел заводит заявку; он же прикладывает счёт.">
                            <UserSelect users={users} value={draft.responsible_user_id} onChange={(v) => set('responsible_user_id', v)} />
                        </Field>
                    </div>
                    {draft.periodicity === 'custom' && (
                        <div className="grid gap-3 sm:grid-cols-2">
                            <Field label="Интервал, дней" required optionalMark={false}><input className={`${iosInput} tabular-nums`} inputMode="numeric" value={draft.interval_days} onChange={(e) => set('interval_days', e.target.value.replace(/\D/g, ''))} /></Field>
                            <Field label="Создавать за, дней" hint="За сколько дней до срока заводить заявку."><input className={`${iosInput} tabular-nums`} inputMode="numeric" value={draft.lead_days} onChange={(e) => set('lead_days', e.target.value.replace(/\D/g, ''))} /></Field>
                        </div>
                    )}

                    <div className="grid gap-3 sm:grid-cols-2">
                        <Field label="Поставщик" hint="Кому платим. Подставится в заявку сам.">
                            <FormSelect value={draft.counterparty_id} onChange={(v) => setDraft((prev) => ({ ...prev, counterparty_id: v || null, contract_id: null, counterparty_account_id: null }))} options={[{ value: '', label: 'Не выбран' }, ...counterpartyOptions]} searchable ariaLabel="Поставщик" />
                        </Field>
                        <Field label="Договор" hint={HINTS.contract}>
                            <FormSelect value={draft.contract_id} onChange={(v) => set('contract_id', v || null)} options={contractOptions} disabled={!draft.counterparty_id} placeholder={draft.counterparty_id ? 'Без договора' : 'Сначала поставщик'} ariaLabel="Договор" />
                        </Field>
                    </div>
                    <div className="grid gap-3 sm:grid-cols-2">
                        <Field label="Компания-плательщик" hint={HINTS.legalEntity.intro}>
                            <FormSelect value={draft.legal_entity_id} onChange={(v) => set('legal_entity_id', v || null)} options={[{ value: '', label: 'Не выбрана' }, ...legalEntityOptions]} ariaLabel="Компания-плательщик" />
                        </Field>
                        <Field label="Реквизиты поставщика" hint="Счёт, на который уходит оплата. Ведутся в справочнике «Банковские реквизиты поставщиков».">
                            <FormSelect value={draft.counterparty_account_id} onChange={(v) => set('counterparty_account_id', v || null)} options={accountOptions} disabled={!draft.counterparty_id} placeholder={draft.counterparty_id ? 'Не выбраны' : 'Сначала поставщик'} ariaLabel="Реквизиты поставщика" />
                        </Field>
                    </div>
                    <Field label="Назначение платежа" hint="Подставится в заявку: «Аренда офиса по договору №12 за …».">
                        <input className={iosInput} value={draft.payment_purpose} onChange={(e) => set('payment_purpose', e.target.value)} maxLength={1000} />
                    </Field>
                    <div className="grid gap-3 sm:grid-cols-2">
                        <Field label="Согласующий" hint="Кому из утверждающих уходят заявки по этому платежу. Пусто — по общим лимитам и маршрутам.">
                            <UserSelect users={users} value={draft.approver_user_id} onChange={(v) => set('approver_user_id', v)} placeholder="По общим правилам" />
                        </Field>
                        <Field label="Лимит, ₸" hint={HINTS.templateLimit}>
                            <AmountInput value={draft.amount_limit} onChange={(v) => set('amount_limit', v)} placeholder="Без лимита" ariaLabel="Лимит" />
                        </Field>
                    </div>

                    <div className="grid gap-3 sm:grid-cols-2">
                        <Field label="Категория"><FormSelect value={draft.category_id} onChange={(v) => setDraft((prev) => ({ ...prev, category_id: v || null, subcategory_id: null }))} options={[{ value: '', label: '—' }, ...categories.map((c) => ({ value: c.id, label: c.name }))]} ariaLabel="Категория" /></Field>
                        {subcategories.length > 0 && (
                            <Field label="Подкатегория"><FormSelect value={draft.subcategory_id} onChange={(v) => set('subcategory_id', v || null)} options={[{ value: '', label: '—' }, ...subcategories.map((c) => ({ value: c.id, label: c.name }))]} ariaLabel="Подкатегория" /></Field>
                        )}
                    </div>
                    <div className="grid gap-3 sm:grid-cols-2">
                        <Field label="Проект"><FormSelect value={draft.project_id} onChange={(v) => set('project_id', v || null)} options={projectOptions} ariaLabel="Проект" /></Field>
                        <Field label="Филиал / регион"><input className={iosInput} value={draft.branch} onChange={(e) => set('branch', e.target.value)} maxLength={200} /></Field>
                    </div>
                    <div className="flex flex-wrap gap-x-6 gap-y-3">
                        <Field as="div" label="Что оплачиваем" optionalMark={false} hint={HINTS.objectType}>
                            <Choice value={draft.object_type} options={OBJECT_TYPE_OPTIONS} onChange={(v) => setDraft((prev) => ({ ...prev, object_type: v, accounting_category: v === 'goods' ? prev.accounting_category : null }))} ariaLabel="Тип объекта" />
                        </Field>
                        {/* П. 10: у товара категория учёта обязательна — заявка возьмёт её отсюда. */}
                        {draft.object_type === 'goods' && (
                            <Field as="div" label="Категория учёта" required optionalMark={false} hint={HINTS.accountingCategory}>
                                <Choice value={draft.accounting_category} options={ACCOUNTING_CATEGORY_OPTIONS} onChange={(v) => set('accounting_category', v)} ariaLabel="Категория учёта приобретения" />
                            </Field>
                        )}
                        <Field as="div" label="Заявка по платежу" optionalMark={false} hint={HINTS.autoCreate}>
                            <Choice value={draft.auto_create} options={AUTO_CREATE_OPTIONS} onChange={(v) => set('auto_create', v)} ariaLabel="Кто заводит заявку" />
                        </Field>
                    </div>
                    <Field label="Примечание"><input className={iosInput} value={draft.note} onChange={(e) => set('note', e.target.value)} maxLength={4000} /></Field>
                    {draft.id && (
                        <Field as="div" label="Состояние" optionalMark={false} hint={HINTS.templateActive}>
                            <Choice value={Boolean(draft.is_active)} options={TEMPLATE_ACTIVE_OPTIONS} onChange={(v) => set('is_active', v)} ariaLabel="Действует ли платёж" />
                        </Field>
                    )}
                </div>
            </IosModal>

            <IosModal
                open={importOpen}
                onClose={() => { setImportOpen(false); setImportRows(null); }}
                title="Импорт регулярных платежей из Excel"
                subtitle={importRows ? `Строк: ${importRows.length} · к загрузке: ${importReady}` : ''}
                maxWidth="max-w-3xl"
                footer={(
                    <>
                        <button type="button" className={iosBtnSecondary} onClick={() => { setImportOpen(false); setImportRows(null); }} disabled={importing}>Отмена</button>
                        <button type="button" className={iosBtnPrimary} onClick={confirmImport} disabled={importing || !importReady}>
                            {importing && <Loader2 size={14} className="animate-spin" />} Загрузить {importReady || ''}
                        </button>
                    </>
                )}
            >
                <div className="space-y-3">
                    <ErrorBox text={importError} />
                    {importRows && importRows.length > 0 && (
                        <>
                            <NoticeBox tone="slate" text="Проверьте разбор: строки с ошибками не загрузятся, недостающие проекты, категории и поставщики будут заведены в справочники. Договор, реквизиты и согласующего допишите в платеже после загрузки." />
                            <div className="overflow-x-auto rounded-xl ring-1 ring-slate-200/70">
                                {/* Ширина — стилем, а не классом min-w-[…]: его оболочка телефона обнуляет. */}
                                <table className="w-full text-[12.5px]" style={{ minWidth: 720 }}>
                                    <thead>
                                        <tr className="bg-slate-50 text-left text-[11px] uppercase tracking-wider text-slate-500">
                                            <th className="px-3 py-2 font-semibold">Наименование</th>
                                            <th className="px-3 py-2 font-semibold">Сумма</th>
                                            <th className="px-3 py-2 font-semibold">Период</th>
                                            <th className="px-3 py-2 font-semibold">Срок</th>
                                            <th className="px-3 py-2 font-semibold">Ответственный</th>
                                            <th className="px-3 py-2 font-semibold">Проверка</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {importRows.map((row, index) => (
                                            <tr key={index} className={`border-t border-slate-100 ${row.errors?.length ? 'bg-rose-50/60' : (row.exists ? 'text-slate-400' : '')}`}>
                                                <td className="px-3 py-1.5 text-slate-900">{row.name || <span className="text-rose-600">—</span>}{row.counterparty ? <div className="text-slate-500">{row.counterparty}{row.counterparty_new ? ' (новый)' : ''}</div> : null}</td>
                                                <td className="px-3 py-1.5 tabular-nums">{fmtMoney(row.amount)}</td>
                                                <td className="px-3 py-1.5">{PERIOD_META[row.periodicity]?.label || row.periodicity}</td>
                                                <td className="px-3 py-1.5 tabular-nums">{row.next_due_on ? fmtDate(row.next_due_on) : <span className="text-rose-600">не разобрана</span>}</td>
                                                <td className="px-3 py-1.5">{row.responsible_name || <span className="text-rose-600">—</span>}</td>
                                                <td className="px-3 py-1.5 text-slate-600">
                                                    {(row.errors || []).map((text) => <div key={text} className="text-rose-700">{text}</div>)}
                                                    {(row.notes || []).map((text) => <div key={text}>{text}</div>)}
                                                    {!row.errors?.length && !row.notes?.length && <span className="text-emerald-700">готово</span>}
                                                </td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        </>
                    )}
                </div>
            </IosModal>
        </div>
    );
};

export default FixedPaymentsPanel;
