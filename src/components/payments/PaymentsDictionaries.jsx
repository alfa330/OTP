import React, { useCallback, useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import { Loader2, Plus } from 'lucide-react';
import { iosBtnPrimary, iosBtnSecondary, iosCard, iosInput, IosModal, IosSegmented, IosToggle } from '../ui/ios';
import CustomSelect from '../ui/CustomSelect';
import IosDatePicker from '../ui/DatePicker';
import {
    CONTRACT_STATUS_META, CONTRACT_STATUS_OPTIONS, ORDER_STATUS_META, PARTY_KIND_OPTIONS, fmtDate, fmtMoney,
    parseAmount, tonePill,
} from './paymentsMeta';
import { AmountInput, ErrorBox, Field, UserSelect, errorText } from './paymentsUi';

/*
 * Справочники раздела: проекты, категории (две ступени), контрагенты, наши
 * юр. лица, договоры и Приказы на согласование.
 *
 * Все шесть устроены одинаково — список и окно правки, поэтому здесь один
 * компонент по схеме, а не шесть копий. Разница в наборе полей описана
 * данными (SCHEMAS): колонки списка и поля формы.
 *
 * Удалять можно только то, на что не ссылаются заявки; иначе сервер ответит
 * 409, а строке предлагается снять галочку «активна» — записанное в заявках
 * не должно терять подпись.
 */

const dateTrigger = 'flex w-full items-center gap-2 rounded-xl bg-slate-100 px-3.5 py-2.5 '
    + 'text-[14px] tabular-nums text-slate-900 border-0 transition hover:bg-slate-200/70 '
    + 'focus:outline-none focus:ring-2 focus:ring-blue-500/70 [&>span]:flex-1 [&>span]:text-left';

const TABS = [
    { value: 'counterparties', label: 'Контрагенты' },
    { value: 'contracts', label: 'Договоры' },
    { value: 'orders', label: 'Приказы' },
    { value: 'categories', label: 'Категории' },
    { value: 'projects', label: 'Проекты' },
    { value: 'legal_entities', label: 'Юр. лица' },
];

const ActivePill = ({ active }) => (active ? null : (
    <span className="rounded-full bg-slate-200 px-2 py-0.5 text-[11.5px] text-slate-600">не активна</span>
));

const StatusPill = ({ meta }) => {
    if (!meta) return null;
    const pill = tonePill(meta.tone);
    return (
        <span className={`inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[11.5px] font-medium ${pill.fill}`}>
            <span className={`h-1.5 w-1.5 rounded-full ${pill.dot}`} />{meta.label}
        </span>
    );
};

const PaymentsDictionaries = ({ apiBaseUrl, headers, users, dictionaries, roles = {}, onDictionariesChanged, showToast, canEdit }) => {
    const [tab, setTab] = useState('counterparties');
    const [rows, setRows] = useState([]);
    const [loading, setLoading] = useState(false);
    const [editing, setEditing] = useState(null);   // {} — новая запись
    const [draft, setDraft] = useState({});
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState('');
    const [confirmDelete, setConfirmDelete] = useState(false);

    const load = useCallback(async () => {
        setLoading(true);
        try {
            const response = await axios.get(`${apiBaseUrl}/api/payments/dictionaries/${tab}`, { headers: headers() });
            setRows(response.data?.items || []);
        } catch (err) {
            showToast?.(errorText(err, 'Не удалось загрузить справочник'), 'error');
        } finally {
            setLoading(false);
        }
    }, [apiBaseUrl, headers, tab, showToast]);

    /* Строки предыдущего справочника сбрасываются ДО загрузки нового: иначе
       один кадр строки контрагентов рисуются отрисовщиком Приказов и падают на
       полях, которых у них нет. */
    useEffect(() => { setRows([]); load(); }, [load]);

    const categoriesRoot = useMemo(() => (tab === 'categories' ? rows : dictionaries?.categories || []).filter((item) => !item.parent_id), [rows, tab, dictionaries]);
    const counterpartyOptions = useMemo(() => (dictionaries?.counterparties || []).map((item) => ({ value: item.id, label: item.name })), [dictionaries]);
    const projectOptions = useMemo(() => (dictionaries?.projects || []).map((item) => ({ value: item.id, label: item.name })), [dictionaries]);
    const legalEntityOptions = useMemo(() => (dictionaries?.legal_entities || []).map((item) => ({ value: item.id, label: item.name })), [dictionaries]);

    const openEditor = (row) => {
        setError('');
        setConfirmDelete(false);
        const base = row || {};
        setEditing(base);
        setDraft({
            id: base.id ?? null,
            name: base.name || '',
            is_active: base.is_active ?? true,
            parent_id: base.parent_id ?? null,
            bin: base.bin || '',
            kind: base.kind || null,
            vat_payer: Boolean(base.vat_payer),
            requisites: base.requisites || '',
            contact: base.contact || '',
            note: base.note || '',
            counterparty_id: base.counterparty_id ?? null,
            legal_entity_id: base.legal_entity_id ?? null,
            number: base.number || '',
            signed_on: base.signed_on ? String(base.signed_on).slice(0, 10) : '',
            starts_on: base.starts_on ? String(base.starts_on).slice(0, 10) : '',
            ends_on: base.ends_on ? String(base.ends_on).slice(0, 10) : '',
            status: base.status || 'active',
            subject: base.subject || '',
            issued_on: base.issued_on ? String(base.issued_on).slice(0, 10) : '',
            // Новому Приказу подставляется Директор по развитию из «Участников» —
            // именно ему по ТЗ передаётся право; при правке остаётся записанный.
            delegate_user_id: base.delegate_user_id ?? (roles?.development_director?.[0]?.user_id ?? null),
            amount_limit: base.amount_limit ?? '',
            all_projects: Boolean(base.all_projects),
            all_counterparties: Boolean(base.all_counterparties),
            project_ids: base.project_ids || [],
            counterparty_ids: base.counterparty_ids || [],
        });
    };

    const set = (key, value) => setDraft((prev) => ({ ...prev, [key]: value }));

    const save = async () => {
        setSaving(true);
        setError('');
        try {
            const payload = { ...draft };
            if (payload.amount_limit === '') payload.amount_limit = null;
            await axios.post(`${apiBaseUrl}/api/payments/dictionaries/${tab}`, payload, { headers: headers() });
            showToast?.('Сохранено', 'success');
            setEditing(null);
            await load();
            onDictionariesChanged?.();
        } catch (err) {
            setError(errorText(err, 'Не удалось сохранить'));
        } finally {
            setSaving(false);
        }
    };

    const remove = async () => {
        if (!draft.id) return;
        setSaving(true);
        try {
            await axios.delete(`${apiBaseUrl}/api/payments/dictionaries/${tab}/${draft.id}`, { headers: headers() });
            showToast?.('Удалено', 'success');
            setEditing(null);
            await load();
            onDictionariesChanged?.();
        } catch (err) {
            setError(errorText(err, 'Не удалось удалить'));
        } finally {
            setSaving(false);
            setConfirmDelete(false);
        }
    };

    const nameOf = (list, id) => (list || []).find((item) => item.value === id)?.label || '—';

    const renderRow = (row) => {
        switch (tab) {
            case 'projects':
                return (
                    <>
                        <div className="min-w-0 flex-1 truncate text-[13.5px] text-slate-900">{row.name}</div>
                        <ActivePill active={row.is_active} />
                    </>
                );
            case 'categories':
                return (
                    <>
                        <div className="min-w-0 flex-1 truncate text-[13.5px] text-slate-900">{row.name}</div>
                        <ActivePill active={row.is_active} />
                    </>
                );
            case 'legal_entities':
                return (
                    <>
                        <div className="min-w-0 flex-1">
                            <div className="truncate text-[13.5px] text-slate-900">{row.name}</div>
                            <div className="truncate text-[12px] text-slate-500">
                                {[row.kind && PARTY_KIND_OPTIONS.find((o) => o.value === row.kind)?.label, row.bin && `БИН ${row.bin}`, row.vat_payer ? 'с НДС' : 'без НДС'].filter(Boolean).join(' · ')}
                            </div>
                        </div>
                        <ActivePill active={row.is_active} />
                    </>
                );
            case 'counterparties':
                return (
                    <>
                        <div className="min-w-0 flex-1">
                            <div className="truncate text-[13.5px] text-slate-900">{row.name}</div>
                            <div className="truncate text-[12px] text-slate-500">
                                {[row.kind && PARTY_KIND_OPTIONS.find((o) => o.value === row.kind)?.label, row.bin && `БИН ${row.bin}`, row.vat_payer ? 'с НДС' : null,
                                    row.active_contracts ? `договоров: ${row.active_contracts}` : 'без действующего договора'].filter(Boolean).join(' · ')}
                            </div>
                        </div>
                        <ActivePill active={row.is_active} />
                    </>
                );
            case 'contracts':
                return (
                    <>
                        <div className="min-w-0 flex-1">
                            <div className="truncate text-[13.5px] text-slate-900">№{row.number} · {row.counterparty_name}</div>
                            <div className="truncate text-[12px] tabular-nums text-slate-500">
                                {row.starts_on ? `с ${fmtDate(row.starts_on)}` : ''}{row.ends_on ? ` до ${fmtDate(row.ends_on)}` : ' · бессрочный'}
                                {row.legal_entity_name ? ` · ${row.legal_entity_name}` : ''}
                            </div>
                        </div>
                        <StatusPill meta={CONTRACT_STATUS_META[row.status]} />
                    </>
                );
            case 'orders':
                return (
                    <>
                        <div className="min-w-0 flex-1">
                            <div className="truncate text-[13.5px] text-slate-900">Приказ №{row.number} от {fmtDate(row.issued_on)} · {row.delegate_name}</div>
                            <div className="truncate text-[12px] text-slate-500">
                                вместо Учредителя · {row.all_projects ? 'все проекты' : `проектов: ${(row.project_ids || []).length}`}
                                {' · '}{row.all_counterparties ? 'все контрагенты' : `контрагентов: ${(row.counterparty_ids || []).length}`}
                                {' · '}{row.amount_limit ? `до ${fmtMoney(row.amount_limit)}` : 'без лимита'}
                                {row.ends_on ? ` · до ${fmtDate(row.ends_on)}` : ''}
                            </div>
                        </div>
                        <StatusPill meta={ORDER_STATUS_META[row.status]} />
                    </>
                );
            default:
                return null;
        }
    };

    const grouped = useMemo(() => {
        if (tab !== 'categories') return null;
        const roots = rows.filter((item) => !item.parent_id);
        return roots.map((root) => ({ root, children: rows.filter((item) => item.parent_id === root.id) }));
    }, [rows, tab]);

    const renderForm = () => {
        switch (tab) {
            case 'projects':
                return (
                    <Field label="Название" required><input className={iosInput} value={draft.name} onChange={(e) => set('name', e.target.value)} maxLength={200} /></Field>
                );
            case 'categories':
                return (
                    <>
                        <Field label="Название" required><input className={iosInput} value={draft.name} onChange={(e) => set('name', e.target.value)} maxLength={200} /></Field>
                        <Field label="Родительская категория" hint="Пусто — категория верхнего уровня. Подкатегория выбирается в заявке после категории.">
                            <CustomSelect value={draft.parent_id} onChange={(v) => set('parent_id', v)} options={[{ value: null, label: 'Категория верхнего уровня' }, ...categoriesRoot.filter((c) => c.id !== draft.id).map((c) => ({ value: c.id, label: c.name }))]} variant="ios" ariaLabel="Родительская категория" />
                        </Field>
                    </>
                );
            case 'legal_entities':
            case 'counterparties':
                return (
                    <>
                        <Field label="Название" required><input className={iosInput} value={draft.name} onChange={(e) => set('name', e.target.value)} maxLength={200} /></Field>
                        <div className="grid gap-3 sm:grid-cols-3">
                            <Field label="Форма"><CustomSelect value={draft.kind} onChange={(v) => set('kind', v)} options={[{ value: null, label: '—' }, ...PARTY_KIND_OPTIONS]} variant="ios" ariaLabel="Форма" /></Field>
                            <Field label="БИН / ИИН"><input className={`${iosInput} tabular-nums`} inputMode="numeric" value={draft.bin} onChange={(e) => set('bin', e.target.value.replace(/\D/g, '').slice(0, 12))} placeholder="12 цифр" /></Field>
                            <Field label="НДС" optionalMark={false}>
                                <div className="flex h-[42px] items-center gap-2 px-1 text-[13.5px] text-slate-700"><IosToggle checked={draft.vat_payer} onChange={(v) => set('vat_payer', v)} /> плательщик</div>
                            </Field>
                        </div>
                        {tab === 'counterparties' && (
                            <>
                                <Field label="Реквизиты" hint="Банк, IBAN, адрес — пригодится при запросе счёта."><textarea className={`${iosInput} min-h-[72px] resize-y`} value={draft.requisites} onChange={(e) => set('requisites', e.target.value)} maxLength={4000} /></Field>
                                <Field label="Контакты"><input className={iosInput} value={draft.contact} onChange={(e) => set('contact', e.target.value)} maxLength={1000} /></Field>
                            </>
                        )}
                        <Field label="Примечание"><input className={iosInput} value={draft.note} onChange={(e) => set('note', e.target.value)} maxLength={2000} /></Field>
                    </>
                );
            case 'contracts':
                return (
                    <>
                        <Field label="Контрагент" required>
                            <CustomSelect value={draft.counterparty_id} onChange={(v) => set('counterparty_id', v)} options={counterpartyOptions} placeholder="Выберите контрагента" variant="ios" searchable ariaLabel="Контрагент" />
                        </Field>
                        <div className="grid gap-3 sm:grid-cols-2">
                            <Field label="Номер договора" required><input className={iosInput} value={draft.number} onChange={(e) => set('number', e.target.value)} maxLength={100} /></Field>
                            <Field label="Статус" required optionalMark={false}>
                                <CustomSelect value={draft.status} onChange={(v) => set('status', v)} options={CONTRACT_STATUS_OPTIONS} variant="ios" ariaLabel="Статус договора" />
                            </Field>
                        </div>
                        <div className="grid gap-3 sm:grid-cols-3">
                            <Field label="Подписан"><IosDatePicker value={draft.signed_on} onChange={(v) => set('signed_on', v || '')} allowEmpty placeholder="Дата" triggerClassName={dateTrigger} ariaLabel="Дата подписания" /></Field>
                            <Field label="Действует с"><IosDatePicker value={draft.starts_on} onChange={(v) => set('starts_on', v || '')} allowEmpty placeholder="Дата" triggerClassName={dateTrigger} ariaLabel="Начало действия" /></Field>
                            <Field label="Действует до" hint="Пусто — бессрочный. Просроченный договор для проверки счёта считается отсутствующим."><IosDatePicker value={draft.ends_on} onChange={(v) => set('ends_on', v || '')} allowEmpty placeholder="Бессрочно" triggerClassName={dateTrigger} ariaLabel="Окончание действия" /></Field>
                        </div>
                        <Field label="Наше юр. лицо"><CustomSelect value={draft.legal_entity_id} onChange={(v) => set('legal_entity_id', v)} options={[{ value: null, label: '—' }, ...legalEntityOptions]} variant="ios" ariaLabel="Юр. лицо" /></Field>
                        <Field label="Предмет договора"><input className={iosInput} value={draft.subject} onChange={(e) => set('subject', e.target.value)} maxLength={2000} /></Field>
                        <Field label="Примечание"><input className={iosInput} value={draft.note} onChange={(e) => set('note', e.target.value)} maxLength={2000} /></Field>
                    </>
                );
            case 'orders':
                return (
                    <>
                        <div className="grid gap-3 sm:grid-cols-2">
                            <Field label="Номер Приказа" required><input className={iosInput} value={draft.number} onChange={(e) => set('number', e.target.value)} maxLength={100} /></Field>
                            <Field label="Дата Приказа" required optionalMark={false}><IosDatePicker value={draft.issued_on} onChange={(v) => set('issued_on', v || '')} allowEmpty placeholder="Дата" triggerClassName={dateTrigger} ariaLabel="Дата Приказа" /></Field>
                        </div>
                        <Field label="Кому передаётся право согласования" required hint="Например, Директор по развитию. Он согласует счёт на шаге 9 вместо Учредителя — только если счёт подходит под все условия ниже.">
                            <UserSelect users={users} value={draft.delegate_user_id} onChange={(v) => set('delegate_user_id', v)} placeholder="Выберите сотрудника" />
                        </Field>
                        <div className="grid gap-3 sm:grid-cols-3">
                            <Field label="Действует с" required optionalMark={false}><IosDatePicker value={draft.starts_on} onChange={(v) => set('starts_on', v || '')} allowEmpty placeholder="Как дата Приказа" triggerClassName={dateTrigger} ariaLabel="Начало действия" /></Field>
                            <Field label="Действует до" hint="Пусто — бессрочно."><IosDatePicker value={draft.ends_on} onChange={(v) => set('ends_on', v || '')} allowEmpty placeholder="Бессрочно" triggerClassName={dateTrigger} ariaLabel="Окончание действия" /></Field>
                            <Field label="Лимит суммы" hint="Счёт согласуется по Приказу, только если сумма не превышает лимит. Пусто — без лимита."><AmountInput value={draft.amount_limit} onChange={(v) => set('amount_limit', v)} placeholder="Без лимита" ariaLabel="Лимит суммы" /></Field>
                        </div>
                        <Field label="Проекты" required optionalMark={false}>
                            <div className="space-y-2">
                                <div className="flex items-center gap-2 px-1 text-[13.5px] text-slate-700"><IosToggle checked={draft.all_projects} onChange={(v) => set('all_projects', v)} /> Все проекты</div>
                                {!draft.all_projects && (
                                    <CustomSelect value={draft.project_ids} onChange={(v) => set('project_ids', v)} options={projectOptions} placeholder="Выберите проекты" variant="ios" multiple searchable ariaLabel="Проекты" />
                                )}
                            </div>
                        </Field>
                        <Field label="Контрагенты" required optionalMark={false} hint="Обязательное условие: поставщик по счёту должен входить в Приказ, иначе применяется стандартный согласующий.">
                            <div className="space-y-2">
                                <div className="flex items-center gap-2 px-1 text-[13.5px] text-slate-700"><IosToggle checked={draft.all_counterparties} onChange={(v) => set('all_counterparties', v)} /> Все контрагенты</div>
                                {!draft.all_counterparties && (
                                    <CustomSelect value={draft.counterparty_ids} onChange={(v) => set('counterparty_ids', v)} options={counterpartyOptions} placeholder="Выберите контрагентов" variant="ios" multiple searchable ariaLabel="Контрагенты" />
                                )}
                            </div>
                        </Field>
                        <Field label="Статус" required optionalMark={false}>
                            <IosSegmented value={draft.status} options={[{ value: 'active', label: 'Действующий' }, { value: 'cancelled', label: 'Отменён' }]} onChange={(v) => set('status', v)} ariaLabel="Статус Приказа" />
                        </Field>
                        <Field label="Примечание"><input className={iosInput} value={draft.note} onChange={(e) => set('note', e.target.value)} maxLength={2000} /></Field>
                    </>
                );
            default:
                return null;
        }
    };

    const tabMeta = TABS.find((item) => item.value === tab);
    const showActiveToggle = ['projects', 'categories', 'legal_entities', 'counterparties'].includes(tab);

    return (
        <div className="space-y-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="overflow-x-auto">
                    <IosSegmented value={tab} options={TABS} onChange={setTab} ariaLabel="Справочник" />
                </div>
                {canEdit && (
                    <button type="button" className={iosBtnPrimary} onClick={() => openEditor(null)}>
                        <Plus size={15} /> Добавить
                    </button>
                )}
            </div>

            <div className={`${iosCard} overflow-hidden`}>
                {loading && !rows.length && (
                    <div className="flex items-center justify-center gap-2 py-10 text-[13px] text-slate-500"><Loader2 size={15} className="animate-spin" /> Загружаем…</div>
                )}
                {!loading && !rows.length && (
                    <div className="px-4 py-10 text-center text-[13.5px] text-slate-500">
                        {tabMeta?.label}: пока пусто{canEdit ? ' — добавьте первую запись' : ''}
                    </div>
                )}
                {tab === 'categories' && grouped && grouped.map(({ root, children }) => (
                    <div key={root.id} className="border-b border-slate-100 last:border-b-0">
                        <button type="button" className="flex w-full items-center gap-3 px-4 py-2.5 text-left transition hover:bg-slate-50" onClick={() => canEdit && openEditor(root)}>
                            <div className="min-w-0 flex-1 truncate text-[13.5px] font-medium text-slate-900">{root.name}</div>
                            <ActivePill active={root.is_active} />
                        </button>
                        {children.map((child) => (
                            <button key={child.id} type="button" className="flex w-full items-center gap-3 py-2 pl-9 pr-4 text-left transition hover:bg-slate-50" onClick={() => canEdit && openEditor(child)}>
                                <div className="min-w-0 flex-1 truncate text-[13px] text-slate-700">{child.name}</div>
                                <ActivePill active={child.is_active} />
                            </button>
                        ))}
                    </div>
                ))}
                {tab !== 'categories' && rows.map((row) => (
                    <button key={row.id} type="button" className="flex w-full items-center gap-3 border-b border-slate-100 px-4 py-2.5 text-left transition last:border-b-0 hover:bg-slate-50" onClick={() => canEdit && openEditor(row)}>
                        {renderRow(row)}
                    </button>
                ))}
            </div>

            <IosModal
                open={Boolean(editing)}
                onClose={() => setEditing(null)}
                title={editing?.id ? `${tabMeta?.label}: правка` : `${tabMeta?.label}: новая запись`}
                maxWidth="max-w-xl"
                footer={(
                    <>
                        {editing?.id && (
                            confirmDelete ? (
                                <div className="mr-auto flex items-center gap-2 text-[12.5px] text-rose-700">
                                    Удалить без возврата?
                                    <button type="button" className={`${iosBtnSecondary} py-1.5 text-rose-700`} disabled={saving} onClick={remove}>Да, удалить</button>
                                    <button type="button" className={`${iosBtnSecondary} py-1.5`} onClick={() => setConfirmDelete(false)}>Нет</button>
                                </div>
                            ) : (
                                <button type="button" className={`${iosBtnSecondary} mr-auto text-rose-600`} disabled={saving} onClick={() => setConfirmDelete(true)}>Удалить</button>
                            )
                        )}
                        <button type="button" className={iosBtnSecondary} onClick={() => setEditing(null)} disabled={saving}>Отмена</button>
                        <button type="button" className={iosBtnPrimary} onClick={save} disabled={saving}>
                            {saving && <Loader2 size={14} className="animate-spin" />} Сохранить
                        </button>
                    </>
                )}
            >
                <div className="space-y-3">
                    <ErrorBox text={error} />
                    {renderForm()}
                    {showActiveToggle && editing?.id && (
                        <div className="flex items-center gap-2 px-1 pt-1 text-[13.5px] text-slate-700">
                            <IosToggle checked={draft.is_active} onChange={(v) => set('is_active', v)} /> активна — предлагается в новых заявках
                        </div>
                    )}
                </div>
            </IosModal>
        </div>
    );
};

export default PaymentsDictionaries;
