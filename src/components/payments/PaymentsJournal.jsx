import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import { ChevronDown, ChevronUp, History, Loader2, ScrollText } from 'lucide-react';
import { iosCard } from '../ui/ios';
import { FIELD_LABELS, describeEvent, fmtDate, fmtDateTime, fmtMoney } from './paymentsMeta';
import { ErrorBox, Field, FormDate, FormInput, FormSelect, Pager, UserSelect, errorText } from './paymentsUi';

/*
 * Журнал раздела (владелец 17.11.2026, п. 10: «логирование всех данных, прям всех»; на вопрос с
 * вариантами — «Изменения и просмотры»). Администратор раздела видит одной лентой всё, что
 * менялось — заявки, строки, файлы, справочники, договоры, регулярные платежи, роли, настройки, —
 * кто, когда и что было → стало, плюс кто открывал заявки и скачивал файлы. У записи справочника —
 * «История» (RecordHistory) с теми же строками о ней одной.
 *
 * Ход заявок сервер берёт из её истории (payment_events), остальное — из журнала раздела
 * (payments/audit.py); здесь обе ленты рисуются одинаково.
 */

const PAGE_SIZE = 50;

// Поля справочников и настроек — словами. Поля заявки — FIELD_LABELS из paymentsMeta.
const JOURNAL_FIELDS = {
    name: 'Название', legal_name: 'Юридическое название', bin: 'БИН', kind: 'Форма', vat_payer: 'НДС',
    requisites: 'Реквизиты', contact: 'Контакт', emails: 'Почта', note: 'Примечание', is_active: 'Действует',
    responsible_user_id: 'Ответственный', approver_user_id: 'Согласующий', approval_limit: 'Лимит согласования',
    category_id: 'Категория', parent_id: 'Родитель', position: 'Порядок', via_helpdesk: 'Helpdesk IT',
    number: 'Номер', signed_on: 'Дата договора', starts_on: 'Действует с', ends_on: 'Действует до', status: 'Статус',
    subject: 'Предмет', amount: 'Сумма', amount_limit: 'Лимит', periodicity: 'Периодичность',
    file_name: 'Файл', file_type: 'Тип файла', file_size: 'Размер файла',
    tax_vat: 'НДС по выписке', tax_regime: 'Режим налогообложения', tax_regime_note: 'Вид СНР', tax_source: 'Источник проверки',
    tax_checked_by: 'Кто проверил', tax_checked_by_name: 'Кто проверил', tax_file_name: 'Выписка КГД',
    tax_file_type: 'Тип выписки', tax_file_size: 'Размер выписки',
    counterparty_id: 'Поставщик', legal_entity_id: 'Компания', counterparty_account_id: 'Реквизиты поставщика',
    contract_id: 'Договор', no_contract: 'Без договора', interval_days: 'Интервал, дней', next_due_on: 'День оплаты',
    lead_days: 'Создавать за, дней', project_id: 'Проект', branch: 'Филиал / регион', subcategory_id: 'Подкатегория',
    payment_purpose: 'Назначение платежа', object_type: 'Тип объекта', accounting_category: 'Категория учёта',
    due_day: 'Число оплаты', auto_create: 'Заявку создаёт раздел', invoice_required: 'Счёт нужен',
    checker_user_id: 'Проверяющий', legal_address: 'Юридический адрес', bank_name: 'Банк', iik: 'ИИК', bik: 'БИК',
    kbe: 'КБе', letters_email: 'Почта для писем', letters_provider: 'Почта (сервис)', is_default: 'Основной',
    owner_kind: 'Чья карта', user_id: 'Сотрудник', holder_name: 'Владелец карты', card_last4: 'Карта',
    card_number: 'Номер карты', delegate_user_id: 'Согласующий', all_projects: 'Все проекты',
    all_counterparties: 'Все поставщики', issued_on: 'Дата приказа', department_id: 'Подразделение',
    payment_method: 'Способ оплаты', request_kind: 'Тип заявки', amount_from: 'Сумма от', amount_to: 'Сумма до',
    manager_step: 'Этап руководителя', email: 'Почта', contact_name: 'Контактное лицо',
    min_suppliers: 'Минимум поставщиков', due_soon_days: 'Напоминать о сроке оплаты, дней', receipt_days: 'Срок чека, дней',
    closing_docs_days: 'Срок закрывающих документов, дней', contract_warn_days: 'Договор «обратите внимание», дней',
    limit_warn_percent: 'Лимит «почти выбран», %', items: 'Позиции', offers: 'Варианты поставщиков',
};

const fieldLabel = (key) => JOURNAL_FIELDS[key] || FIELD_LABELS[key] || key;

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;
const ISO_TIME = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/;

/* Значение «было / стало» словами: да/нет, дата, сумма, список. */
const valueText = (key, value) => {
    if (value === null || value === undefined || value === '') return '—';
    if (value === true) return 'да';
    if (value === false) return 'нет';
    if (Array.isArray(value)) return value.length ? value.join('; ') : '—';
    if (typeof value === 'object') return JSON.stringify(value);
    const text = String(value);
    if (ISO_TIME.test(text)) return fmtDateTime(text);
    if (ISO_DATE.test(text)) return fmtDate(text);
    if (/(amount|limit|price|sum)/.test(key) && /^-?\d+(\.\d+)?$/.test(text)) return fmtMoney(text);
    return text;
};

/* Что поменялось — таблицей «поле: было → стало». У правки заявки — ещё позиции и поставщики целиком. */
const Changes = ({ changes, details }) => {
    const rows = Object.entries(changes || {}).filter(([key]) => !(details && details[key]));
    const lists = Object.entries(details || {});
    if (!rows.length && !lists.length) return null;
    return (
        <div className="mt-1.5 space-y-1 rounded-lg bg-slate-50 px-2.5 py-1.5 text-[12.5px]" data-journal-changes>
            {rows.map(([key, pair]) => {
                const [before, after] = Array.isArray(pair) ? pair : [null, pair];
                return (
                    <div key={key} className="flex flex-wrap gap-x-2">
                        <span className="w-[160px] shrink-0 text-slate-500">{fieldLabel(key)}</span>
                        <span className="min-w-0 break-words text-slate-400 line-through decoration-slate-300">{valueText(key, before)}</span>
                        <span className="text-slate-400">→</span>
                        <span className="min-w-0 break-words text-slate-900">{valueText(key, after)}</span>
                    </div>
                );
            })}
            {lists.map(([key, [before, after]]) => (
                <div key={key} className="space-y-0.5">
                    <div className="text-slate-500">{fieldLabel(key)}</div>
                    <div className="grid gap-x-3 sm:grid-cols-2">
                        <ul className="list-inside list-disc text-slate-400">{(before || []).map((line) => <li key={`b-${line}`}>{line}</li>)}</ul>
                        <ul className="list-inside list-disc text-slate-900">{(after || []).map((line) => <li key={`a-${line}`}>{line}</li>)}</ul>
                    </div>
                </div>
            ))}
        </div>
    );
};

/* Одна строка журнала. Ход заявки — словами её истории, остальное — «действие · что». */
const JournalRow = ({ row, titleOf, onOpenRequest, showObject = true }) => {
    const [open, setOpen] = useState(false);
    const isEvent = row.source === 'event';
    const details = isEvent ? row.payload?.details : null;
    const changes = row.changes || null;
    const expandable = Boolean((changes && Object.keys(changes).length) || (details && Object.keys(details).length));
    const what = isEvent
        ? describeEvent({ kind: row.action, payload: row.payload || {} }, titleOf)
        : [row.action_label, row.title].filter(Boolean).join(': ');
    const objectText = row.entity === 'request' || row.request_id
        ? `заявка №${row.request_id || row.entity_id}`
        : [row.entity_label, row.entity_id ? `№${row.entity_id}` : ''].filter(Boolean).join(' ');
    return (
        <li className="py-2" data-journal-row={row.source} data-journal-action={row.action}>
            <div className="flex items-start gap-3">
                <span className="w-[112px] shrink-0 pt-px text-[12px] tabular-nums text-slate-500">{fmtDateTime(row.created_at)}</span>
                <div className="min-w-0 flex-1">
                    <div className="text-[13.5px] text-slate-900">
                        {what}
                        {row.comment && <span className="text-slate-500"> — {row.comment}</span>}
                    </div>
                    <div className="text-[12px] text-slate-500">
                        {row.actor_name || 'Раздел'}
                        {showObject && (
                            <>
                                {' · '}
                                {(row.request_id || row.entity === 'request') && onOpenRequest ? (
                                    <button type="button" data-open-request={row.request_id || row.entity_id} onClick={() => onOpenRequest(row.request_id || row.entity_id)}
                                        className="font-medium text-blue-600 underline-offset-2 hover:underline">{objectText}</button>
                                ) : objectText}
                            </>
                        )}
                    </div>
                    {open && <Changes changes={changes} details={details} />}
                </div>
                {expandable && (
                    <button type="button" aria-expanded={open} aria-label={open ? 'Скрыть изменения' : 'Что изменилось'}
                        onClick={() => setOpen((prev) => !prev)}
                        className="grid h-7 w-7 shrink-0 place-items-center rounded-full text-slate-400 transition hover:bg-slate-100 hover:text-slate-700">
                        {open ? <ChevronUp size={15} /> : <ChevronDown size={15} />}
                    </button>
                )}
            </div>
        </li>
    );
};

/* «История» записи справочника: что с ней делали, новые сверху. Закрыта, пока её не открыли. */
export const RecordHistory = ({ apiBaseUrl, headers, entity, entityId, meta = null }) => {
    const [open, setOpen] = useState(false);
    const [rows, setRows] = useState(null);
    const [error, setError] = useState('');
    const titleOf = useCallback((kind) => (meta?.subtasks || []).find((item) => item.kind === kind)?.title || '', [meta]);
    useEffect(() => { setOpen(false); setRows(null); setError(''); }, [entity, entityId]);
    useEffect(() => {
        if (!open || rows || !entityId) return undefined;
        let cancelled = false;
        axios.get(`${apiBaseUrl}/api/payments/journal/${entity}/${entityId}`, { headers: headers() })
            .then((response) => { if (!cancelled) setRows(response.data?.items || []); })
            .catch((err) => { if (!cancelled) setError(errorText(err, 'Не удалось загрузить историю')); });
        return () => { cancelled = true; };
    }, [open, rows, apiBaseUrl, headers, entity, entityId]);
    if (!entityId) return null;
    return (
        <div data-record-history={entity}>
            <button type="button" aria-expanded={open} onClick={() => setOpen((prev) => !prev)}
                className="inline-flex items-center gap-1.5 rounded-lg px-1 py-1 text-[13px] font-medium text-slate-600 transition hover:text-slate-900">
                <History size={14} /> История изменений {open ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
            </button>
            {open && (
                <div className="mt-1 rounded-xl bg-white px-3 ring-1 ring-slate-200">
                    <ErrorBox text={error} />
                    {!rows && !error && <div className="flex items-center gap-2 py-3 text-[13px] text-slate-500"><Loader2 size={14} className="animate-spin" /> Загружаем…</div>}
                    {rows && !rows.length && <div className="py-3 text-[13px] text-slate-500">Изменений пока не было — журнал ведётся с 17.11.2026.</div>}
                    {rows && rows.length > 0 && (
                        <ul className="divide-y divide-slate-100">
                            {rows.map((row) => <JournalRow key={`${row.source}:${row.id}`} row={row} titleOf={titleOf} showObject={false} />)}
                        </ul>
                    )}
                </div>
            )}
        </div>
    );
};

/* Экран «Журнал» — администратору раздела. */
const PaymentsJournal = ({ apiBaseUrl, headers, users, meta, onOpenRequest, showToast }) => {
    const [filters, setFilters] = useState({ from: '', to: '', actor_id: null, entity: '', action: '', request_id: '' });
    const [page, setPage] = useState(1);
    const [rows, setRows] = useState([]);
    const [total, setTotal] = useState(0);
    const [labels, setLabels] = useState({ entities: {}, actions: {} });
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');
    const ticket = useRef(0);
    const titleOf = useCallback((kind) => (meta?.subtasks || []).find((item) => item.kind === kind)?.title || '', [meta]);
    const toastRef = useRef(showToast);
    useEffect(() => { toastRef.current = showToast; }, [showToast]);

    const load = useCallback(async () => {
        const mine = ticket.current + 1;
        ticket.current = mine;
        setLoading(true);
        setError('');
        try {
            const params = { page, page_size: PAGE_SIZE };
            Object.entries(filters).forEach(([key, value]) => { if (value !== '' && value !== null) params[key] = value; });
            const response = await axios.get(`${apiBaseUrl}/api/payments/journal`, { headers: headers(), params });
            if (ticket.current !== mine) return;
            setRows(response.data?.items || []);
            setTotal(response.data?.total || 0);
            setLabels({ entities: response.data?.entities || {}, actions: response.data?.actions || {} });
        } catch (err) {
            if (ticket.current === mine) setError(errorText(err, 'Не удалось загрузить журнал'));
        } finally {
            if (ticket.current === mine) setLoading(false);
        }
    }, [apiBaseUrl, headers, filters, page]);

    useEffect(() => { load(); }, [load]);

    const set = (key, value) => { setPage(1); setFilters((prev) => ({ ...prev, [key]: value })); };
    const entityOptions = useMemo(() => [{ value: '', label: 'Всё' },
        ...Object.entries(labels.entities).map(([value, label]) => ({ value, label }))], [labels.entities]);
    const actionOptions = useMemo(() => [{ value: '', label: 'Любое' },
        ...Object.entries(labels.actions).map(([value, label]) => ({ value, label }))], [labels.actions]);

    return (
        <div className="space-y-3" data-payments-journal>
            <div className={`${iosCard} grid gap-3 p-4 sm:grid-cols-2 lg:grid-cols-6`}>
                <Field label="С"><FormDate value={filters.from} onChange={(value) => set('from', value || '')} ariaLabel="Журнал с" /></Field>
                <Field label="По"><FormDate value={filters.to} onChange={(value) => set('to', value || '')} ariaLabel="Журнал по" /></Field>
                <Field label="Кто"><UserSelect users={users} value={filters.actor_id} onChange={(value) => set('actor_id', value || null)} placeholder="Все" /></Field>
                <Field label="Что"><FormSelect value={filters.entity} onChange={(value) => set('entity', value || '')} options={entityOptions} ariaLabel="Что" /></Field>
                <Field label="Действие"><FormSelect value={filters.action} onChange={(value) => set('action', value || '')} options={actionOptions} ariaLabel="Действие" /></Field>
                <Field label="Заявка №">
                    <FormInput value={filters.request_id} inputMode="numeric" placeholder="Любая"
                        onChange={(event) => set('request_id', event.target.value.replace(/\D/g, '').slice(0, 9))} />
                </Field>
            </div>
            <ErrorBox text={error} />
            <div className={`${iosCard} px-4`}>
                {loading && !rows.length && (
                    <div className="flex items-center justify-center gap-2 py-10 text-[13px] text-slate-500"><Loader2 size={15} className="animate-spin" /> Загружаем…</div>
                )}
                {!loading && !rows.length && !error && (
                    <div className="flex flex-col items-center gap-2 py-10 text-center text-[13.5px] text-slate-500">
                        <ScrollText size={22} className="text-slate-300" />
                        Записей по этому отбору нет.
                    </div>
                )}
                {rows.length > 0 && (
                    <ul className="divide-y divide-slate-100">
                        {rows.map((row) => <JournalRow key={`${row.source}:${row.id}`} row={row} titleOf={titleOf} onOpenRequest={onOpenRequest} />)}
                    </ul>
                )}
            </div>
            <Pager page={page} pageSize={PAGE_SIZE} total={total} loading={loading && rows.length > 0} onPage={setPage} hideSingle />
        </div>
    );
};

export default PaymentsJournal;
