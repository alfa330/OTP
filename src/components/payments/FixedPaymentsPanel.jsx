import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import { CalendarClock, Download, Loader2, Play, Plus, Upload } from 'lucide-react';
import { iosBtnGhost, iosBtnPrimary, iosBtnSecondary, iosCard, iosInput, IosMenu, IosModal, IosSegmented, IosToggle } from '../ui/ios';
import CustomSelect from '../ui/CustomSelect';
import IosDatePicker from '../ui/DatePicker';
import { Pencil, Trash2 } from 'lucide-react';
import { PERIOD_META, PERIOD_OPTIONS, SOURCE_OPTIONS, fmtDate, fmtMoney } from './paymentsMeta';
import { AmountInput, ErrorBox, Field, NoticeBox, UserSelect, errorText } from './paymentsUi';

/*
 * Календарь фиксированных платежей (дополнение Дмитриевой, п. 11).
 *
 * Список регулярных расходов с периодичностью и ближайшим сроком; система сама
 * заводит по ним заявки в начале периода (планировщик утром по Алматы) и
 * ставит «на согласование» с ответственным из настроек. Здесь же — импорт
 * перечня из Excel с предпросмотром и ручной запуск «создать заявки сегодня».
 *
 * В списке только то, что нужно, чтобы понять «что, сколько, когда, кто»;
 * остальные поля — в окне платежа.
 */

const dateTrigger = 'flex w-full items-center gap-2 rounded-xl bg-slate-100 px-3.5 py-2.5 '
    + 'text-[14px] tabular-nums text-slate-900 border-0 transition hover:bg-slate-200/70 '
    + 'focus:outline-none focus:ring-2 focus:ring-blue-500/70 [&>span]:flex-1 [&>span]:text-left';

const emptyDraft = (me) => ({
    id: null, name: '', amount: 0, periodicity: 'monthly', interval_days: '', next_due_on: '', lead_days: 7,
    project_id: null, branch: '', responsible_user_id: me?.id || null, category_id: null, subcategory_id: null,
    counterparty_id: null, legal_entity_id: null, payment_source: 'too', note: '', is_active: true,
});

const FixedPaymentsPanel = ({ apiBaseUrl, headers, users, dictionaries, me, showToast, canEdit, onRequestsChanged, onOpenRequest }) => {
    const [rows, setRows] = useState([]);
    const [loading, setLoading] = useState(false);
    const [editing, setEditing] = useState(false);
    const [draft, setDraft] = useState(() => emptyDraft(me));
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState('');
    const [running, setRunning] = useState(false);
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
            toastRef.current?.(errorText(err, 'Не удалось загрузить календарь'), 'error');
        } finally {
            setLoading(false);
        }
    }, [apiBaseUrl, headers]);

    useEffect(() => { load(); }, [load]);

    const set = (key, value) => setDraft((prev) => ({ ...prev, [key]: value }));

    const categories = useMemo(() => (dictionaries?.categories || []).filter((item) => !item.parent_id), [dictionaries]);
    const subcategories = useMemo(() => (dictionaries?.categories || []).filter((item) => item.parent_id === draft.category_id), [dictionaries, draft.category_id]);
    const projectOptions = useMemo(() => [{ value: null, label: 'Без проекта' }, ...(dictionaries?.projects || []).map((item) => ({ value: item.id, label: item.name }))], [dictionaries]);
    const counterpartyOptions = useMemo(() => [{ value: null, label: '—' }, ...(dictionaries?.counterparties || []).map((item) => ({ value: item.id, label: item.name }))], [dictionaries]);
    const legalEntityOptions = useMemo(() => [{ value: null, label: '—' }, ...(dictionaries?.legal_entities || []).map((item) => ({ value: item.id, label: item.name }))], [dictionaries]);

    const openEditor = (row) => {
        setError('');
        setDraft(row ? {
            id: row.id, name: row.name || '', amount: Number(row.amount) || 0, periodicity: row.periodicity || 'monthly',
            interval_days: row.interval_days || '', next_due_on: row.next_due_on ? String(row.next_due_on).slice(0, 10) : '',
            lead_days: row.lead_days ?? 7, project_id: row.project_id ?? null, branch: row.branch || '',
            responsible_user_id: row.responsible_user_id ?? null, category_id: row.category_id ?? null,
            subcategory_id: row.subcategory_id ?? null, counterparty_id: row.counterparty_id ?? null,
            legal_entity_id: row.legal_entity_id ?? null, payment_source: row.payment_source || 'too', note: row.note || '',
            is_active: row.is_active ?? true,
        } : emptyDraft(me));
        setEditing(true);
    };

    const save = async () => {
        setSaving(true);
        setError('');
        try {
            await axios.post(`${apiBaseUrl}/api/payments/templates`, draft, { headers: headers() });
            toastRef.current?.('Платёж сохранён', 'success');
            setEditing(false);
            load();
        } catch (err) {
            setError(errorText(err, 'Не удалось сохранить'));
        } finally {
            setSaving(false);
        }
    };

    const remove = async (row) => {
        try {
            await axios.delete(`${apiBaseUrl}/api/payments/templates/${row.id}`, { headers: headers() });
            toastRef.current?.('Платёж удалён из календаря', 'success');
            load();
        } catch (err) {
            toastRef.current?.(errorText(err, 'Не удалось удалить'), 'error');
        }
    };

    const generateNow = async (row) => {
        try {
            const response = await axios.post(`${apiBaseUrl}/api/payments/templates/${row.id}/generate`, {}, { headers: headers() });
            toastRef.current?.(`Заявка №${response.data?.request_id} создана`, 'success');
            load();
            onRequestsChanged?.();
            if (response.data?.request_id) onOpenRequest?.(response.data.request_id);
        } catch (err) {
            toastRef.current?.(errorText(err, 'Не удалось создать заявку'), 'error');
        }
    };

    const runDue = async () => {
        setRunning(true);
        try {
            const response = await axios.post(`${apiBaseUrl}/api/payments/templates/generate`, {}, { headers: headers() });
            const count = (response.data?.created || []).length;
            toastRef.current?.(count ? `Создано заявок: ${count}` : 'Сегодня заявок по календарю не требуется', count ? 'success' : 'info');
            load();
            onRequestsChanged?.();
        } catch (err) {
            toastRef.current?.(errorText(err, 'Не удалось запустить календарь'), 'error');
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
            toastRef.current?.(`Загружено платежей: ${response.data?.created || 0}${response.data?.skipped ? `, пропущено: ${response.data.skipped}` : ''}`, 'success');
            setImportOpen(false);
            setImportRows(null);
            load();
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
            link.download = 'Календарь фиксированных платежей.xlsx';
            document.body.appendChild(link);
            link.click();
            link.remove();
            setTimeout(() => URL.revokeObjectURL(url), 1000);
        } catch (err) {
            toastRef.current?.(errorText(err, 'Не удалось скачать образец'), 'error');
        }
    };

    const dueCount = rows.filter((row) => row.is_due).length;

    return (
        <div className="space-y-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="text-[13px] text-slate-500">
                    Заявки создаются сами в начале периода{dueCount ? <> · <span className="font-medium text-slate-700">готовы к созданию: {dueCount}</span></> : ''}
                </div>
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
                        Календарь пуст. Добавьте платёж или загрузите перечень из Excel.
                    </div>
                )}
                {rows.map((row, index) => (
                    <div key={row.id} className={`flex items-center gap-3 px-4 py-2.5 ${index > 0 ? 'border-t border-slate-100' : ''} ${row.is_active ? '' : 'opacity-60'}`}>
                        <div className="min-w-0 flex-1">
                            <div className="flex flex-wrap items-baseline gap-x-2">
                                <span className="truncate text-[13.5px] font-medium text-slate-900">{row.name}</span>
                                <span className="text-[12.5px] text-slate-500">{PERIOD_META[row.periodicity]?.label || row.periodicity}{row.periodicity === 'custom' && row.interval_days ? ` (${row.interval_days} дн.)` : ''}</span>
                            </div>
                            <div className="truncate text-[12px] text-slate-500">
                                {row.responsible_name || '—'}
                                {row.counterparty_name ? ` · ${row.counterparty_name}` : ''}
                                {row.project_name ? ` · ${row.project_name}` : ''}
                                {row.requests_count ? ` · заявок: ${row.requests_count}` : ''}
                            </div>
                        </div>
                        <div className="hidden text-right sm:block">
                            <div className="text-[13.5px] tabular-nums text-slate-900">{fmtMoney(row.amount)}</div>
                            <div className="text-[12px] tabular-nums text-slate-500">
                                срок {fmtDate(row.next_due_on)}{row.is_due ? <span className="ml-1 text-amber-700">· пора создать</span> : (row.generate_on ? ` · заявка ${fmtDate(row.generate_on)}` : '')}
                            </div>
                        </div>
                        {row.open_request_id && (
                            <button type="button" className="hidden shrink-0 text-[12.5px] text-blue-600 hover:underline sm:block" onClick={() => onOpenRequest?.(row.open_request_id)}>
                                заявка №{row.open_request_id}
                            </button>
                        )}
                        {canEdit && (
                            <IosMenu
                                label="Действия с платежом"
                                items={[
                                    { key: 'edit', label: 'Изменить', icon: Pencil, onSelect: () => openEditor(row) },
                                    { key: 'now', label: 'Создать заявку сейчас', icon: Play, onSelect: () => generateNow(row), hint: fmtDate(row.next_due_on) },
                                    { key: 'del', label: 'Удалить из календаря', icon: Trash2, danger: true, separatorBefore: true, onSelect: () => remove(row) },
                                ]}
                            />
                        )}
                    </div>
                ))}
            </div>

            <IosModal
                open={editing}
                onClose={() => setEditing(false)}
                title={draft.id ? 'Фиксированный платёж: правка' : 'Новый фиксированный платёж'}
                maxWidth="max-w-xl"
                footer={(
                    <>
                        <button type="button" className={iosBtnSecondary} onClick={() => setEditing(false)} disabled={saving}>Отмена</button>
                        <button type="button" className={iosBtnPrimary} onClick={save} disabled={saving}>{saving && <Loader2 size={14} className="animate-spin" />} Сохранить</button>
                    </>
                )}
            >
                <div className="space-y-3">
                    <ErrorBox text={error} />
                    <Field label="Наименование" required><input className={iosInput} value={draft.name} onChange={(e) => set('name', e.target.value)} maxLength={300} placeholder="Аренда офиса Алматы" /></Field>
                    <div className="grid gap-3 sm:grid-cols-2">
                        <Field label="Сумма" required><AmountInput value={draft.amount} onChange={(v) => set('amount', v)} ariaLabel="Сумма" /></Field>
                        <Field label="Периодичность" required>
                            <CustomSelect value={draft.periodicity} onChange={(v) => set('periodicity', v)} options={PERIOD_OPTIONS} variant="ios" ariaLabel="Периодичность" />
                        </Field>
                    </div>
                    <div className="grid gap-3 sm:grid-cols-2">
                        <Field label="Ближайшая дата оплаты" required hint="Заявка создастся первого числа этого месяца (у своего интервала — за N дней до срока) и после этого срок сдвинется на период вперёд.">
                            <IosDatePicker value={draft.next_due_on} onChange={(v) => set('next_due_on', v || '')} allowEmpty placeholder="Дата" triggerClassName={dateTrigger} ariaLabel="Ближайшая дата оплаты" />
                        </Field>
                        {draft.periodicity === 'custom' ? (
                            <Field label="Интервал, дней" required><input className={`${iosInput} tabular-nums`} inputMode="numeric" value={draft.interval_days} onChange={(e) => set('interval_days', e.target.value.replace(/\D/g, ''))} /></Field>
                        ) : (
                            <Field label="Ответственный" required><UserSelect users={users} value={draft.responsible_user_id} onChange={(v) => set('responsible_user_id', v)} /></Field>
                        )}
                    </div>
                    {draft.periodicity === 'custom' && (
                        <div className="grid gap-3 sm:grid-cols-2">
                            <Field label="Ответственный" required><UserSelect users={users} value={draft.responsible_user_id} onChange={(v) => set('responsible_user_id', v)} /></Field>
                            <Field label="Создавать за, дней" hint="За сколько дней до срока заводить заявку."><input className={`${iosInput} tabular-nums`} inputMode="numeric" value={draft.lead_days} onChange={(e) => set('lead_days', e.target.value.replace(/\D/g, ''))} /></Field>
                        </div>
                    )}
                    <div className="grid gap-3 sm:grid-cols-2">
                        <Field label="Категория"><CustomSelect value={draft.category_id} onChange={(v) => setDraft((prev) => ({ ...prev, category_id: v, subcategory_id: null }))} options={[{ value: null, label: '—' }, ...categories.map((c) => ({ value: c.id, label: c.name }))]} variant="ios" ariaLabel="Категория" /></Field>
                        <Field label="Подкатегория"><CustomSelect value={draft.subcategory_id} onChange={(v) => set('subcategory_id', v)} options={[{ value: null, label: '—' }, ...subcategories.map((c) => ({ value: c.id, label: c.name }))]} disabled={!subcategories.length} variant="ios" ariaLabel="Подкатегория" /></Field>
                    </div>
                    <div className="grid gap-3 sm:grid-cols-2">
                        <Field label="Контрагент"><CustomSelect value={draft.counterparty_id} onChange={(v) => set('counterparty_id', v)} options={counterpartyOptions} variant="ios" searchable ariaLabel="Контрагент" /></Field>
                        <Field label="Проект"><CustomSelect value={draft.project_id} onChange={(v) => set('project_id', v)} options={projectOptions} variant="ios" ariaLabel="Проект" /></Field>
                    </div>
                    <div className="grid gap-3 sm:grid-cols-2">
                        <Field label="Юр. лицо плательщика"><CustomSelect value={draft.legal_entity_id} onChange={(v) => set('legal_entity_id', v)} options={legalEntityOptions} variant="ios" ariaLabel="Юр. лицо" /></Field>
                        <Field label="Источник оплаты" optionalMark={false}><IosSegmented value={draft.payment_source} options={SOURCE_OPTIONS} onChange={(v) => set('payment_source', v)} stretch ariaLabel="Источник оплаты" /></Field>
                    </div>
                    <Field label="Филиал / регион"><input className={iosInput} value={draft.branch} onChange={(e) => set('branch', e.target.value)} maxLength={200} /></Field>
                    <Field label="Примечание"><input className={iosInput} value={draft.note} onChange={(e) => set('note', e.target.value)} maxLength={4000} /></Field>
                    {draft.id && (
                        <div className="flex items-center gap-2 px-1 text-[13.5px] text-slate-700"><IosToggle checked={draft.is_active} onChange={(v) => set('is_active', v)} /> активен — заявки создаются</div>
                    )}
                </div>
            </IosModal>

            <IosModal
                open={importOpen}
                onClose={() => { setImportOpen(false); setImportRows(null); }}
                title="Импорт календаря из Excel"
                subtitle={importRows ? `Строк: ${importRows.length} · готовы: ${importRows.filter((r) => !r.errors?.length).length}` : ''}
                maxWidth="max-w-3xl"
                footer={(
                    <>
                        <button type="button" className={iosBtnSecondary} onClick={() => { setImportOpen(false); setImportRows(null); }} disabled={importing}>Отмена</button>
                        <button type="button" className={iosBtnPrimary} onClick={confirmImport} disabled={importing || !importRows || !importRows.some((r) => !r.errors?.length)}>
                            {importing && <Loader2 size={14} className="animate-spin" />} Загрузить {importRows ? importRows.filter((r) => !r.errors?.length).length : ''}
                        </button>
                    </>
                )}
            >
                <div className="space-y-3">
                    <ErrorBox text={importError} />
                    {importRows && importRows.length > 0 && (
                        <>
                            <NoticeBox tone="slate" text="Проверьте разбор: строки с ошибками не загрузятся, недостающие проекты, категории и контрагенты будут заведены в справочники." />
                            <div className="overflow-x-auto rounded-xl ring-1 ring-slate-200/70">
                                <table className="w-full min-w-[720px] text-[12.5px]">
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
                                            <tr key={index} className={`border-t border-slate-100 ${row.errors?.length ? 'bg-rose-50/60' : ''}`}>
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
