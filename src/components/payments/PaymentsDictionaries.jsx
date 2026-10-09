import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import { Download, FileText, Loader2, Paperclip, Plus, Search, Trash2 } from 'lucide-react';
import { iosBtnGhost, iosBtnPrimary, iosBtnSecondary, iosCard, iosInput, IosModal } from '../ui/ios';
import CustomSelect from '../ui/CustomSelect';
import IosDatePicker from '../ui/DatePicker';
import FixedPaymentsPanel from './FixedPaymentsPanel';
import {
    CONTRACT_STATUS_META, CONTRACT_STATUS_OPTIONS, HINTS, KIND_SHORT_OPTIONS, LIMIT_STATUS_META, MANAGER_STEP_OPTIONS,
    METHOD_SHORT_OPTIONS, PARTY_KIND_OPTIONS, PAYMENT_METHOD_OPTIONS, PERIOD_META, PERIOD_OPTIONS, REQUEST_KIND_OPTIONS,
    DICTIONARY_PAGE_SIZE, RECORD_FORMS, VAT_OPTIONS, cardNumberLabel, fileSizeLabel, fmtDate, fmtMoney, pageRange, parseAmount,
} from './paymentsMeta';
import {
    AmountInput, CardNumber, Choice, CopyButton, DATE_TRIGGER, ErrorBox, Field, FormSelect, Pager, TonePill, UserSelect, errorText,
} from './paymentsUi';

/*
 * Справочники раздела (ТЗ «Закуп и оплата», п. 13) — восемь названных в ТЗ, теми
 * же словами и в том же порядке: поставщики, договоры, реквизиты компаний,
 * банковские реквизиты поставщиков, банковские карты сотрудников/поставщиков,
 * регулярные платежи, лимиты и маршруты согласования. Ниже — служебные списки,
 * из которых выбирает форма заявки (категории, проекты, категории имущества).
 *
 * Все справочники устроены одинаково — список и окно записи, поэтому здесь один
 * компонент, а разница описана данными: чем подписана строка и какие поля в окне.
 * Исключение — «Регулярные платежи»: у них ещё календарь и импорт из Excel,
 * поэтому их экран живёт в своём компоненте (FixedPaymentsPanel).
 *
 * Кто ведёт: администратор раздела; карты — финансовый отдел (в них полные
 * номера), категории имущества — ответственный за учёт. Остальным справочник
 * открыт для чтения: запись открывается, но поля в ней закрыты.
 *
 * Удалять можно только то, на что не ссылаются заявки; иначе сервер ответит
 * отказом, а запись предлагается скрыть — записанное в заявках не должно
 * терять подпись.
 */

const SECTIONS = [
    { value: 'counterparties', label: 'Поставщики', about: 'Кому платим. Согласующий и лимит поставщика направляют его заявки нужному утверждающему.' },
    { value: 'contracts', label: 'Договоры', about: 'Договоры с поставщиками. Счёт свыше 300 000 ₸ проходит только по действующему договору.' },
    { value: 'legal_entities', label: 'Реквизиты компаний', about: 'Наши компании-плательщики. Реквизиты подставляются в заявку и копируются одним щелчком.' },
    { value: 'counterparty_accounts', label: 'Банковские реквизиты поставщиков', about: 'Банковские счета поставщиков, на которые уходит оплата.' },
    { value: 'cards', label: 'Банковские карты сотрудников/поставщиков', need: 'can_manage_cards', about: 'Карты для пополнения. Полные номера видит только финансовый отдел.' },
    { value: 'templates', label: 'Регулярные платежи', need: (caps) => caps.is_admin || caps.sees_all_requests, about: 'Аренда, связь, лицензии: поставщик, договор и реквизиты подставляются в заявку сами.' },
    { value: 'limits', label: 'Лимиты согласования', about: 'Кто, до какой суммы и по каким поставщикам утверждает заявки.' },
    { value: 'routes', label: 'Маршруты согласования', about: 'Кто утверждает, когда ни один лимит не подошёл, и нужен ли этап руководителя.' },
    { value: 'categories', label: 'Категории закупа', about: 'По категориям группируются затраты и выбирается согласующий.' },
    { value: 'asset_categories', label: 'Категории имущества', about: 'Виды имущества для постановки на учёт: техника, мебель, оборудование.' },
    { value: 'projects', label: 'Проекты', about: 'Проекты, к которым относят расходы.' },
    { value: 'settings', label: 'Настройки', need: 'is_admin', about: 'Общие правила процесса.' },
];

const ACTIVE_OPTIONS = [{ value: true, label: 'Активна' }, { value: false, label: 'Скрыта' }];
const ROUTE_ACTIVE_OPTIONS = [{ value: true, label: 'Действует' }, { value: false, label: 'Отключён' }];
const SCOPE_OPTIONS = [{ value: true, label: 'Все' }, { value: false, label: 'Выбранные' }];
const LIMIT_STATUS_OPTIONS = [{ value: 'active', label: 'Действующий' }, { value: 'cancelled', label: 'Отменён' }];
const CARD_OWNER_OPTIONS = [{ value: 'employee', label: 'Сотрудника' }, { value: 'supplier', label: 'Поставщика' }];
const DEFAULT_ACCOUNT_OPTIONS = [{ value: true, label: 'Основной' }, { value: false, label: 'Дополнительный' }];
const WITH_ACTIVE_FLAG = ['projects', 'categories', 'asset_categories', 'legal_entities', 'counterparties', 'counterparty_accounts', 'cards'];
const FILE_ACCEPT = '.pdf,.jpg,.jpeg,.png,.webp,.heic,.doc,.docx';
const SEARCH_FROM = 9;

const iso = (value) => (value ? String(value).slice(0, 10) : '');
const orBlank = (value) => (value === null || value === undefined ? '' : value);
const moneyOrNull = (value) => (parseAmount(value) > 0 ? parseAmount(value) : null);
const nameOf = (options, id) => (options || []).find((item) => item.value === id)?.label || '';

const toDraft = (name, row = {}) => {
    const base = { id: row.id ?? null, name: row.name || '', note: row.note || '', is_active: row.is_active ?? true };
    switch (name) {
        case 'categories':
            return { ...base, parent_id: row.parent_id ?? null, position: row.position ?? 0 };
        case 'legal_entities':
            return {
                ...base, bin: row.bin || '', kind: row.kind || null, vat_payer: Boolean(row.vat_payer),
                legal_address: row.legal_address || '', bank_name: row.bank_name || '', iik: row.iik || '',
                bik: row.bik || '', kbe: row.kbe || '', requisites: row.requisites || '',
            };
        case 'counterparties':
            return {
                ...base, legal_name: row.legal_name || '', bin: row.bin || '', kind: row.kind || null,
                vat_payer: Boolean(row.vat_payer), requisites: row.requisites || '', contact: row.contact || '',
                category_id: row.category_id ?? null, responsible_user_id: row.responsible_user_id ?? null,
                approver_user_id: row.approver_user_id ?? null, approval_limit: orBlank(row.approval_limit),
            };
        case 'counterparty_accounts':
            return {
                id: row.id ?? null, counterparty_id: row.counterparty_id ?? null, bank_name: row.bank_name || '',
                iik: row.iik || '', bik: row.bik || '', kbe: row.kbe || '', note: row.note || '',
                is_default: Boolean(row.is_default), is_active: row.is_active ?? true,
            };
        case 'contracts':
            return {
                id: row.id ?? null, counterparty_id: row.counterparty_id ?? null, legal_entity_id: row.legal_entity_id ?? null,
                number: row.number || '', signed_on: iso(row.signed_on), starts_on: iso(row.starts_on), ends_on: iso(row.ends_on),
                status: row.status || 'active', subject: row.subject || '', amount: orBlank(row.amount),
                amount_limit: orBlank(row.amount_limit), periodicity: row.periodicity || '',
                responsible_user_id: row.responsible_user_id ?? null, note: row.note || '',
            };
        case 'cards':
            return {
                id: row.id ?? null, owner_kind: row.owner_kind || 'employee', user_id: row.user_id ?? null,
                counterparty_id: row.counterparty_id ?? null, holder_name: row.holder_name || '', card_number: '',
                note: row.note || '', is_active: row.is_active ?? true,
            };
        case 'limits':
            return {
                id: row.id ?? null, delegate_user_id: row.delegate_user_id ?? null, amount_limit: orBlank(row.amount_limit),
                all_projects: row.id ? Boolean(row.all_projects) : true,
                all_counterparties: row.id ? Boolean(row.all_counterparties) : true,
                project_ids: row.project_ids || [], counterparty_ids: row.counterparty_ids || [],
                legal_entity_id: row.legal_entity_id ?? null, department_id: row.department_id ?? null,
                category_id: row.category_id ?? null, payment_method: row.payment_method || '',
                request_kind: row.request_kind || '', number: row.number || '', issued_on: iso(row.issued_on),
                starts_on: iso(row.starts_on), ends_on: iso(row.ends_on), status: row.status || 'active', note: row.note || '',
            };
        case 'routes':
            return {
                ...base, position: row.position ?? 0, legal_entity_id: row.legal_entity_id ?? null,
                department_id: row.department_id ?? null, category_id: row.category_id ?? null,
                payment_method: row.payment_method || '', request_kind: row.request_kind || '',
                amount_from: orBlank(row.amount_from), amount_to: orBlank(row.amount_to),
                manager_step: row.manager_step || 'auto', approver_user_id: row.approver_user_id ?? null,
            };
        default:
            return base;
    }
};

/* Черновик → то, что уходит на сервер: пустые суммы и «любой» — это null. */
const toPayload = (name, draft) => {
    const payload = { ...draft };
    ['approval_limit', 'amount', 'amount_limit', 'amount_from', 'amount_to'].forEach((key) => {
        if (key in payload) payload[key] = moneyOrNull(payload[key]);
    });
    ['payment_method', 'request_kind', 'periodicity'].forEach((key) => {
        if (key in payload && !payload[key]) payload[key] = null;
    });
    if (name === 'cards' && !payload.card_number) delete payload.card_number;
    return payload;
};

const conditionsLine = (row) => [
    row.legal_entity_name, row.department_name, row.category_name,
    nameOf(PAYMENT_METHOD_OPTIONS, row.payment_method), nameOf(REQUEST_KIND_OPTIONS, row.request_kind),
].filter(Boolean).join(' · ');

/* Текст строки для поиска по списку. */
const rowText = (row) => [
    row.name, row.legal_name, row.bin, row.number, row.counterparty_name, row.legal_entity_name, row.holder_name,
    row.owner_name, row.delegate_name, row.approver_name, row.iik, row.bank_name, row.subject,
].filter(Boolean).join(' ').toLowerCase();

const Hidden = ({ active, label = 'скрыта' }) => (active ? null : (
    <span className="shrink-0 rounded-full bg-slate-200 px-2 py-0.5 text-[11.5px] text-slate-600">{label}</span>
));

const Settings = ({ apiBaseUrl, headers, showToast, onSaved }) => {
    const [values, setValues] = useState(null);
    const [limits, setLimits] = useState({});
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState('');

    useEffect(() => {
        let cancelled = false;
        axios.get(`${apiBaseUrl}/api/payments/settings`, { headers: headers() })
            .then((response) => {
                if (cancelled) return;
                setValues(response.data?.settings || {});
                setLimits(response.data?.limits || {});
            })
            .catch((err) => { if (!cancelled) setError(errorText(err, 'Не удалось загрузить настройки')); });
        return () => { cancelled = true; };
    }, [apiBaseUrl, headers]);

    const save = async () => {
        setSaving(true);
        setError('');
        try {
            const response = await axios.post(`${apiBaseUrl}/api/payments/settings`, values, { headers: headers() });
            setValues(response.data?.settings || values);
            showToast?.('Настройки сохранены', 'success');
            onSaved?.();
        } catch (err) {
            setError(errorText(err, 'Не удалось сохранить'));
        } finally {
            setSaving(false);
        }
    };

    if (!values) return <div className={`${iosCard} p-4`}>{error ? <ErrorBox text={error} /> : <div className="flex items-center gap-2 text-[13px] text-slate-500"><Loader2 size={15} className="animate-spin" /> Загружаем…</div>}</div>;
    const numberField = (key, label, hint, unit) => {
        const [low, high] = limits[key] || [0, 99];
        return (
            <Field label={label} required optionalMark={false} hint={hint}>
                <div className="flex items-center gap-2">
                    <input
                        className={`${iosInput} w-24 tabular-nums`}
                        inputMode="numeric"
                        aria-label={label}
                        value={values[key] ?? ''}
                        onChange={(event) => setValues((prev) => ({ ...prev, [key]: event.target.value.replace(/\D/g, '').slice(0, 2) }))}
                    />
                    <span className="text-[12.5px] text-slate-500">{unit} · от {low} до {high}</span>
                </div>
            </Field>
        );
    };
    return (
        <div className={`${iosCard} space-y-4 p-4`}>
            <ErrorBox text={error} />
            {numberField('min_suppliers', 'Минимум поставщиков в новом закупе', HINTS.minSuppliers.intro, 'вариантов')}
            {numberField('due_soon_days', 'Напоминать о сроке за', HINTS.dueSoonDays.intro, 'дней')}
            <div>
                <button type="button" className={iosBtnPrimary} onClick={save} disabled={saving}>
                    {saving && <Loader2 size={14} className="animate-spin" />} Сохранить
                </button>
            </div>
        </div>
    );
};

/* Экраны, у которых нет общего списка с окном записи. */
const OWN_SCREENS = ['settings', 'templates'];

const PaymentsDictionaries = ({
    apiBaseUrl, headers, users, dictionaries, capabilities = {}, me, onDictionariesChanged, onSettingsChanged,
    onRequestsChanged, onOpenRequest, showToast,
}) => {
    const sections = useMemo(() => SECTIONS.filter((item) => !item.need
        || (typeof item.need === 'function' ? item.need(capabilities) : capabilities[item.need])), [capabilities]);
    const [tab, setTab] = useState('counterparties');
    const current = sections.find((item) => item.value === tab) || sections[0];
    const name = current?.value;
    const ownScreen = OWN_SCREENS.includes(name);

    const [rows, setRows] = useState([]);
    const [canEdit, setCanEdit] = useState(false);
    const [loading, setLoading] = useState(false);
    const [filter, setFilter] = useState('');
    const [editing, setEditing] = useState(null);   // строка справочника; {} — новая запись
    const [draft, setDraft] = useState({});
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState('');
    const [confirmDelete, setConfirmDelete] = useState(false);
    const [contractFile, setContractFile] = useState(null);
    const fileRef = useRef(null);

    const toastRef = useRef(showToast);
    useEffect(() => { toastRef.current = showToast; }, [showToast]);

    // Билет запроса: при быстром переключении справочников поздний ответ прежнего
    // не должен лечь под заголовок нового — запись открылась бы с чужим id.
    const ticketRef = useRef(0);
    const load = useCallback(async () => {
        const ticket = ticketRef.current + 1;
        ticketRef.current = ticket;
        if (!name || OWN_SCREENS.includes(name)) { setLoading(false); return; }
        setLoading(true);
        try {
            const response = await axios.get(`${apiBaseUrl}/api/payments/dictionaries/${name}`, { headers: headers() });
            if (ticketRef.current !== ticket) return;
            setRows(response.data?.items || []);
            setCanEdit(Boolean(response.data?.can_edit));
        } catch (err) {
            if (ticketRef.current === ticket) toastRef.current?.(errorText(err, 'Не удалось загрузить справочник'), 'error');
        } finally {
            if (ticketRef.current === ticket) setLoading(false);
        }
    }, [apiBaseUrl, headers, name]);

    /* Строки предыдущего справочника сбрасываются ДО загрузки нового: иначе
       один кадр строки поставщиков рисуются отрисовщиком лимитов. */
    useEffect(() => { setRows([]); setFilter(''); setCanEdit(false); load(); }, [load]);

    const categoryRoots = useMemo(() => (name === 'categories' ? rows : dictionaries?.categories || []).filter((item) => !item.parent_id), [rows, name, dictionaries]);
    const counterpartyOptions = useMemo(() => (dictionaries?.counterparties || []).map((item) => ({ value: item.id, label: item.name })), [dictionaries]);
    const projectOptions = useMemo(() => (dictionaries?.projects || []).map((item) => ({ value: item.id, label: item.name })), [dictionaries]);
    const entityOptions = useMemo(() => (dictionaries?.legal_entities || []).map((item) => ({ value: item.id, label: item.name })), [dictionaries]);
    const departmentOptions = useMemo(() => (dictionaries?.departments || []).map((item) => ({ value: item.id, label: item.name })), [dictionaries]);
    const categoryOptions = useMemo(() => (dictionaries?.categories || []).filter((item) => !item.parent_id).map((item) => ({ value: item.id, label: item.name })), [dictionaries]);
    const anyOf = (options, label) => [{ value: '', label }, ...options];

    const openEditor = (row) => {
        setError('');
        setConfirmDelete(false);
        setContractFile(null);
        setEditing(row || {});
        setDraft(toDraft(name, row || {}));
    };
    const set = (key, value) => setDraft((prev) => ({ ...prev, [key]: value }));
    const changed = async () => { await load(); onDictionariesChanged?.(); };

    const save = async () => {
        setSaving(true);
        setError('');
        try {
            const response = await axios.post(`${apiBaseUrl}/api/payments/dictionaries/${name}`, toPayload(name, draft), { headers: headers() });
            // Запись уже есть: если дальше не загрузится файл, повторное «Сохранить»
            // должно править её же, а не заводить вторую такую же.
            if (response.data?.id && !draft.id) {
                setDraft((prev) => ({ ...prev, id: response.data.id }));
                setEditing((prev) => ({ ...(prev || {}), id: response.data.id }));
            }
            if (name === 'contracts' && contractFile && response.data?.id) {
                const form = new FormData();
                form.append('file', contractFile, contractFile.name);
                await axios.post(`${apiBaseUrl}/api/payments/dictionaries/contracts/${response.data.id}/file`, form, { headers: headers() });
            }
            toastRef.current?.('Сохранено', 'success');
            setEditing(null);
            await changed();
        } catch (err) {
            setError(errorText(err, 'Не удалось сохранить'));
            // Запись могла сохраниться, а файл — нет: список показывает, что есть на деле.
            load();
        } finally {
            setSaving(false);
        }
    };

    const remove = async () => {
        if (!draft.id) return;
        setSaving(true);
        try {
            await axios.delete(`${apiBaseUrl}/api/payments/dictionaries/${name}/${draft.id}`, { headers: headers() });
            toastRef.current?.('Удалено', 'success');
            setEditing(null);
            await changed();
        } catch (err) {
            setError(errorText(err, 'Не удалось удалить'));
        } finally {
            setSaving(false);
            setConfirmDelete(false);
        }
    };

    const downloadContract = async (row) => {
        try {
            const response = await axios.get(`${apiBaseUrl}/api/payments/dictionaries/contracts/${row.id}/file`, { headers: headers(), responseType: 'blob' });
            const url = URL.createObjectURL(response.data);
            const link = document.createElement('a');
            link.href = url;
            link.download = row.file_name || 'Договор';
            document.body.appendChild(link);
            link.click();
            link.remove();
            setTimeout(() => URL.revokeObjectURL(url), 1000);
        } catch (err) {
            toastRef.current?.(errorText(err, 'Не удалось скачать файл'), 'error');
        }
    };

    const dropContractFile = async () => {
        setSaving(true);
        try {
            await axios.delete(`${apiBaseUrl}/api/payments/dictionaries/contracts/${draft.id}/file`, { headers: headers() });
            setEditing((prev) => ({ ...prev, has_file: false, file_name: null, file_size: null }));
            toastRef.current?.('Файл снят', 'success');
            load();
        } catch (err) {
            setError(errorText(err, 'Не удалось снять файл'));
        } finally {
            setSaving(false);
        }
    };

    const loadCardNumber = useCallback(async (cardId) => {
        const response = await axios.get(`${apiBaseUrl}/api/payments/dictionaries/cards/${cardId}/number`, { headers: headers() });
        return response.data;
    }, [apiBaseUrl, headers]);

    const shown = useMemo(() => {
        const needle = filter.trim().toLowerCase();
        return needle ? rows.filter((row) => rowText(row).includes(needle)) : rows;
    }, [rows, filter]);

    /* Строка списка: главное — первой строкой, уточнения — второй; справа метка
       состояния и то, что делают, не открывая запись (скопировать, показать номер). */
    const rowView = (row) => {
        switch (name) {
            case 'legal_entities':
                return {
                    title: row.name,
                    sub: [nameOf(PARTY_KIND_OPTIONS, row.kind), row.bin && `БИН ${row.bin}`, row.iik && `ИИК ${row.iik}`, row.vat_payer ? 'с НДС' : 'без НДС'].filter(Boolean).join(' · '),
                    side: <><CopyButton text={row.requisites_text} label="Скопировать реквизиты" /><Hidden active={row.is_active} /></>,
                };
            case 'counterparties':
                return {
                    title: row.name,
                    sub: [nameOf(PARTY_KIND_OPTIONS, row.kind), row.bin && `БИН ${row.bin}`, row.vat_payer ? 'с НДС' : 'без НДС', row.category_name,
                        row.active_contracts ? `действующих договоров: ${row.active_contracts}` : 'без действующего договора',
                        row.approver_name && `согласует ${row.approver_name}${row.approval_limit ? ` до ${fmtMoney(row.approval_limit)}` : ''}`].filter(Boolean).join(' · '),
                    side: <Hidden active={row.is_active} />,
                };
            case 'counterparty_accounts':
                return {
                    title: row.counterparty_name,
                    sub: row.text,
                    side: <>{row.is_default && <TonePill tone="neutral">основной</TonePill>}<Hidden active={row.is_active} label="скрыт" /></>,
                };
            case 'contracts':
                return {
                    title: `№${row.number} · ${row.counterparty_name || '—'}`,
                    sub: [row.signed_on && `от ${fmtDate(row.signed_on)}`, row.starts_on && `с ${fmtDate(row.starts_on)}`, row.ends_on ? `до ${fmtDate(row.ends_on)}` : 'бессрочный',
                        row.legal_entity_name, row.amount && fmtMoney(row.amount), row.periodicity && (PERIOD_META[row.periodicity]?.label || '').toLowerCase(),
                        row.has_file ? 'файл приложен' : 'без файла'].filter(Boolean).join(' · '),
                    side: CONTRACT_STATUS_META[row.status] && <TonePill tone={CONTRACT_STATUS_META[row.status].tone}>{CONTRACT_STATUS_META[row.status].label}</TonePill>,
                };
            case 'cards':
                return {
                    title: row.holder_name || row.owner_name,
                    sub: [row.owner_kind === 'employee' ? 'карта сотрудника' : `карта поставщика${row.owner_name ? ` ${row.owner_name}` : ''}`, row.note].filter(Boolean).join(' · '),
                    side: <><CardNumber mask={row.mask} canReveal load={() => loadCardNumber(row.id)} showToast={showToast} /><Hidden active={row.is_active} /></>,
                };
            case 'limits':
                return {
                    title: `${row.delegate_name || '—'} · ${row.amount_limit ? `до ${fmtMoney(row.amount_limit)}` : 'без лимита суммы'}`,
                    sub: [row.all_counterparties ? 'все поставщики' : `поставщиков: ${(row.counterparty_ids || []).length}`,
                        row.all_projects ? null : `проектов: ${(row.project_ids || []).length}`, conditionsLine(row),
                        row.number && `Приказ №${row.number}${row.issued_on ? ` от ${fmtDate(row.issued_on)}` : ''}`,
                        row.ends_on && `до ${fmtDate(row.ends_on)}`].filter(Boolean).join(' · '),
                    side: LIMIT_STATUS_META[row.status] && <TonePill tone={LIMIT_STATUS_META[row.status].tone}>{LIMIT_STATUS_META[row.status].label}</TonePill>,
                };
            case 'routes':
                return {
                    title: row.name,
                    sub: [conditionsLine(row) || 'любая заявка',
                        (row.amount_from || row.amount_to) && `сумма${row.amount_from ? ` от ${fmtMoney(row.amount_from)}` : ''}${row.amount_to ? ` до ${fmtMoney(row.amount_to)}` : ''}`,
                        `руководитель: ${nameOf(MANAGER_STEP_OPTIONS, row.manager_step || 'auto').toLowerCase()}`,
                        row.approver_name ? `утверждает ${row.approver_name}` : 'утверждает любой из утверждающих'].filter(Boolean).join(' · '),
                    side: <Hidden active={row.is_active} label="отключён" />,
                };
            default:
                return { title: row.name, sub: '', side: <Hidden active={row.is_active} /> };
        }
    };

    const grouped = useMemo(() => {
        if (name !== 'categories') return null;
        const roots = shown.filter((item) => !item.parent_id);
        const orphans = shown.filter((item) => item.parent_id && !roots.some((root) => root.id === item.parent_id));
        return [...roots.map((root) => ({ root, children: shown.filter((item) => item.parent_id === root.id) })),
            ...orphans.map((root) => ({ root, children: [] }))];
    }, [shown, name]);

    /* Список — страницами по двадцать, как списки раздела «Задачи». Справочник
       приходит целиком, листаем на месте. Номер страницы помнится вместе со
       справочником и поиском: другой список или новый запрос — с первой страницы.
       Категории листаются группами: подкатегория не уезжает от своей категории. */
    const listKey = `${name}|${filter.trim().toLowerCase()}`;
    const [listPaging, setListPaging] = useState({ key: '', page: 1 });
    const listItems = grouped || shown;
    const listRange = pageRange(listPaging.key === listKey ? listPaging.page : 1, DICTIONARY_PAGE_SIZE, listItems.length);
    const pageItems = listItems.slice(listRange.offset, listRange.offset + DICTIONARY_PAGE_SIZE);

    const text = (key, props = {}) => (
        <input className={iosInput} value={draft[key] ?? ''} onChange={(event) => set(key, event.target.value)} {...props} />
    );
    const date = (key, label, placeholder = 'Дата') => (
        <IosDatePicker value={draft[key]} onChange={(value) => set(key, value || '')} allowEmpty placeholder={placeholder} triggerClassName={DATE_TRIGGER} ariaLabel={label} />
    );

    /* Условия, общие для лимита и маршрута (п. 6): компания, подразделение, тип
       расхода, тип платежа, регулярность. Пустое условие — «любое». */
    const conditionFields = (
        <>
            <div className="grid gap-3 sm:grid-cols-2">
                <Field label="Компания" optionalMark={false} hint={HINTS.limitAny}>
                    <FormSelect value={draft.legal_entity_id} onChange={(v) => set('legal_entity_id', v || null)} options={anyOf(entityOptions, 'Любая')} ariaLabel="Компания" />
                </Field>
                <Field label="Подразделение" optionalMark={false} hint={HINTS.limitAny}>
                    <FormSelect value={draft.department_id} onChange={(v) => set('department_id', v || null)} options={anyOf(departmentOptions, 'Любое')} searchable ariaLabel="Подразделение" />
                </Field>
            </div>
            <div className="grid gap-3 sm:grid-cols-3">
                <Field label="Тип расхода" optionalMark={false} hint="Категория закупа. Пусто — любая.">
                    <FormSelect value={draft.category_id} onChange={(v) => set('category_id', v || null)} options={anyOf(categoryOptions, 'Любой')} ariaLabel="Тип расхода" />
                </Field>
            </div>
            <div className="flex flex-wrap gap-x-6 gap-y-3">
                <Field as="div" label="Тип платежа" optionalMark={false} hint={HINTS.limitMethod}>
                    <Choice value={draft.payment_method} options={[{ value: '', label: 'Любой' }, ...METHOD_SHORT_OPTIONS]} onChange={(v) => set('payment_method', v)} ariaLabel="Тип платежа" />
                </Field>
                <Field as="div" label="Регулярность" optionalMark={false} hint={HINTS.limitKind}>
                    <Choice value={draft.request_kind} options={[{ value: '', label: 'Любая' }, ...KIND_SHORT_OPTIONS]} onChange={(v) => set('request_kind', v)} ariaLabel="Регулярность" />
                </Field>
            </div>
        </>
    );

    const partyFields = (
        <>
            <div className="flex flex-wrap gap-x-6 gap-y-3">
                <Field as="div" label="Форма" hint={HINTS.partyKind}>
                    <Choice value={draft.kind} options={PARTY_KIND_OPTIONS} onChange={(v) => set('kind', v)} ariaLabel="Форма" />
                </Field>
                <Field as="div" label="НДС" optionalMark={false} hint={HINTS.vatPayer}>
                    <Choice value={Boolean(draft.vat_payer)} options={VAT_OPTIONS} onChange={(v) => set('vat_payer', v)} ariaLabel="НДС" />
                </Field>
            </div>
            <Field label="БИН / ИИН" hint="12 цифр.">
                <input className={`${iosInput} tabular-nums`} inputMode="numeric" value={draft.bin} onChange={(event) => set('bin', event.target.value.replace(/\D/g, '').slice(0, 12))} placeholder="12 цифр" />
            </Field>
        </>
    );

    const bankFields = (
        <>
            <Field label="ИИК" required={name === 'counterparty_accounts'} optionalMark={name !== 'counterparty_accounts'} hint="Номер счёта (IBAN): KZ и 18 знаков.">
                <input className={`${iosInput} tabular-nums`} value={draft.iik} onChange={(event) => set('iik', event.target.value.toUpperCase().replace(/\s/g, ''))} maxLength={34} placeholder="KZ…" />
            </Field>
            <div className="grid gap-3 sm:grid-cols-3">
                <Field label="Банк" className="sm:col-span-1">{text('bank_name', { maxLength: 200 })}</Field>
                <Field label="БИК">{text('bik', { maxLength: 16 })}</Field>
                <Field label="КБЕ">{text('kbe', { maxLength: 4, inputMode: 'numeric' })}</Field>
            </div>
        </>
    );

    const renderForm = () => {
        switch (name) {
            case 'categories':
                return (
                    <>
                        <Field label="Название" required optionalMark={false}>{text('name', { maxLength: 200 })}</Field>
                        <Field label="Родительская категория" hint="Пусто — категория верхнего уровня. Подкатегория выбирается в заявке после категории.">
                            <FormSelect value={draft.parent_id} onChange={(v) => set('parent_id', v || null)} options={[{ value: '', label: 'Категория верхнего уровня' }, ...categoryRoots.filter((item) => item.id !== draft.id).map((item) => ({ value: item.id, label: item.name }))]} ariaLabel="Родительская категория" />
                        </Field>
                    </>
                );
            case 'legal_entities':
                return (
                    <>
                        <Field label="Название" required optionalMark={false}>{text('name', { maxLength: 200 })}</Field>
                        {partyFields}
                        <Field label="Юридический адрес">{text('legal_address', { maxLength: 1000 })}</Field>
                        {bankFields}
                        <Field label="Прочие реквизиты" hint="Всё, чего нет в полях выше: корреспондентский счёт, свидетельство по НДС. Попадёт в «Скопировать реквизиты» последней строкой.">
                            <textarea className={`${iosInput} min-h-[72px] resize-y`} value={draft.requisites} onChange={(event) => set('requisites', event.target.value)} maxLength={4000} />
                        </Field>
                        <Field label="Примечание">{text('note', { maxLength: 2000 })}</Field>
                    </>
                );
            case 'counterparties': {
                const accounts = (dictionaries?.counterparty_accounts || []).filter((item) => item.counterparty_id === draft.id);
                const contracts = (dictionaries?.contracts || []).filter((item) => item.counterparty_id === draft.id && item.status === 'active');
                return (
                    <>
                        <div className="grid gap-3 sm:grid-cols-2">
                            <Field label="Наименование" required optionalMark={false} hint="Как поставщика называют в заявках.">{text('name', { maxLength: 200 })}</Field>
                            <Field label="Юридическое наименование" hint="Как в договоре и счёте: ТОО «…».">{text('legal_name', { maxLength: 300 })}</Field>
                        </div>
                        {partyFields}
                        <div className="grid gap-3 sm:grid-cols-2">
                            <Field label="Контакт">{text('contact', { maxLength: 1000, placeholder: 'Имя, телефон, почта' })}</Field>
                            <Field label="Категория" hint="Чем поставщик обычно занимается — категория закупа.">
                                <FormSelect value={draft.category_id} onChange={(v) => set('category_id', v || null)} options={anyOf(categoryOptions, 'Не выбрана')} ariaLabel="Категория" />
                            </Field>
                        </div>
                        <Field label="Ответственный" hint="Кто в компании ведёт этого поставщика.">
                            <UserSelect users={users} value={draft.responsible_user_id} onChange={(v) => set('responsible_user_id', v)} placeholder="Не назначен" />
                        </Field>
                        <div className="grid gap-3 sm:grid-cols-2">
                            <Field label="Согласующий" hint={HINTS.supplierApprover}>
                                <UserSelect users={users} value={draft.approver_user_id} onChange={(v) => set('approver_user_id', v)} placeholder="По общим правилам" />
                            </Field>
                            <Field label="Лимит согласования, ₸" hint="До какой суммы заявка на этого поставщика уходит его согласующему. Пусто — на любую сумму.">
                                <AmountInput value={draft.approval_limit} onChange={(v) => set('approval_limit', v)} placeholder="Без лимита" ariaLabel="Лимит согласования" />
                            </Field>
                        </div>
                        {draft.id && (
                            <div className="grid gap-3 sm:grid-cols-2">
                                <Field as="div" label="Банковские реквизиты" optionalMark={false} hint="Ведутся в справочнике «Банковские реквизиты поставщиков».">
                                    <div className="space-y-1 rounded-xl bg-slate-50 px-3 py-2 text-[12.5px] text-slate-700">
                                        {accounts.length ? accounts.map((item) => <div key={item.id} className="break-words">{item.text}</div>) : <span className="text-slate-500">не заведены</span>}
                                    </div>
                                </Field>
                                <Field as="div" label="Действующие договоры" optionalMark={false} hint="Ведутся в справочнике «Договоры».">
                                    <div className="space-y-1 rounded-xl bg-slate-50 px-3 py-2 text-[12.5px] text-slate-700">
                                        {contracts.length ? contracts.map((item) => <div key={item.id}>№{item.number}{item.ends_on ? ` до ${fmtDate(item.ends_on)}` : ' · бессрочный'}</div>) : <span className="text-slate-500">нет</span>}
                                    </div>
                                </Field>
                            </div>
                        )}
                        <Field label="Прочие реквизиты" hint="Адрес и то, чего нет в банковских реквизитах.">
                            <textarea className={`${iosInput} min-h-[60px] resize-y`} value={draft.requisites} onChange={(event) => set('requisites', event.target.value)} maxLength={4000} />
                        </Field>
                        <Field label="Примечание">{text('note', { maxLength: 2000 })}</Field>
                    </>
                );
            }
            case 'counterparty_accounts':
                return (
                    <>
                        <Field label="Поставщик" required optionalMark={false}>
                            <FormSelect value={draft.counterparty_id} onChange={(v) => set('counterparty_id', v)} options={counterpartyOptions} placeholder="Выберите поставщика" searchable ariaLabel="Поставщик" />
                        </Field>
                        {bankFields}
                        <Field as="div" label="Счёт" optionalMark={false} hint="Основной счёт подставляется в заявку первым. Основной у поставщика один.">
                            <Choice value={Boolean(draft.is_default)} options={DEFAULT_ACCOUNT_OPTIONS} onChange={(v) => set('is_default', v)} ariaLabel="Основной ли счёт" />
                        </Field>
                        <Field label="Примечание">{text('note', { maxLength: 2000 })}</Field>
                    </>
                );
            case 'contracts':
                return (
                    <>
                        <div className="grid gap-3 sm:grid-cols-2">
                            <Field label="Поставщик" required optionalMark={false}>
                                <FormSelect value={draft.counterparty_id} onChange={(v) => set('counterparty_id', v)} options={counterpartyOptions} placeholder="Выберите поставщика" searchable ariaLabel="Поставщик" />
                            </Field>
                            <Field label="Компания" hint="Наша компания — сторона договора.">
                                <FormSelect value={draft.legal_entity_id} onChange={(v) => set('legal_entity_id', v || null)} options={anyOf(entityOptions, 'Не выбрана')} ariaLabel="Компания" />
                            </Field>
                        </div>
                        <div className="grid gap-3 sm:grid-cols-2">
                            <Field label="Номер договора" required optionalMark={false}>{text('number', { maxLength: 100 })}</Field>
                            <Field label="Дата договора">{date('signed_on', 'Дата договора')}</Field>
                        </div>
                        <Field label="Предмет договора">{text('subject', { maxLength: 2000 })}</Field>
                        <div className="grid gap-3 sm:grid-cols-2">
                            <Field label="Действует с">{date('starts_on', 'Начало действия')}</Field>
                            <Field label="Действует до" hint="Пусто — бессрочный. Просроченный договор для проверки счёта считается отсутствующим.">{date('ends_on', 'Окончание действия', 'Бессрочно')}</Field>
                        </div>
                        <div className="grid gap-3 sm:grid-cols-3">
                            <Field label="Сумма, ₸"><AmountInput value={draft.amount} onChange={(v) => set('amount', v)} placeholder="Не указана" ariaLabel="Сумма договора" /></Field>
                            <Field label="Лимит, ₸" hint="Предельная сумма оплат по договору, если она оговорена."><AmountInput value={draft.amount_limit} onChange={(v) => set('amount_limit', v)} placeholder="Без лимита" ariaLabel="Лимит договора" /></Field>
                            <Field label="Периодичность" hint="Как часто платим по договору.">
                                <FormSelect value={draft.periodicity} onChange={(v) => set('periodicity', v || '')} options={[{ value: '', label: 'Разовый' }, ...PERIOD_OPTIONS]} ariaLabel="Периодичность" />
                            </Field>
                        </div>
                        <div className="grid gap-3 sm:grid-cols-2">
                            <Field label="Ответственный"><UserSelect users={users} value={draft.responsible_user_id} onChange={(v) => set('responsible_user_id', v)} placeholder="Не назначен" /></Field>
                            <Field label="Статус" required optionalMark={false} hint={HINTS.contractStatus}>
                                <FormSelect value={draft.status} onChange={(v) => set('status', v)} options={CONTRACT_STATUS_OPTIONS} ariaLabel="Статус договора" />
                            </Field>
                        </div>
                        <Field as="div" label="Файл договора" hint="Скан подписанного договора: PDF, изображение или Word.">
                            {editing?.has_file && !contractFile && (
                                <div className="mb-2 flex items-center gap-2 rounded-xl bg-slate-100 px-3 py-2">
                                    <FileText size={15} className="shrink-0 text-slate-400" />
                                    <div className="min-w-0 flex-1">
                                        <div className="truncate text-[13px] text-slate-800">{editing.file_name}</div>
                                        <div className="text-[11.5px] text-slate-500">{fileSizeLabel(editing.file_size)}</div>
                                    </div>
                                    <button type="button" aria-label="Скачать договор" className="grid h-7 w-7 shrink-0 place-items-center rounded-full text-slate-400 transition hover:bg-slate-200 hover:text-slate-700" onClick={() => downloadContract(editing)}><Download size={14} /></button>
                                    <button type="button" aria-label="Снять файл" className="grid h-7 w-7 shrink-0 place-items-center rounded-full text-slate-400 transition hover:bg-rose-50 hover:text-rose-600" onClick={dropContractFile}><Trash2 size={14} /></button>
                                </div>
                            )}
                            {contractFile && (
                                <div className="mb-2 flex items-center gap-2 rounded-xl bg-slate-100 px-3 py-2">
                                    <FileText size={15} className="shrink-0 text-slate-400" />
                                    <div className="min-w-0 flex-1 truncate text-[13px] text-slate-800">{contractFile.name} <span className="text-slate-500">· {fileSizeLabel(contractFile.size)} · загрузится при сохранении</span></div>
                                    <button type="button" aria-label="Убрать файл" className="grid h-7 w-7 shrink-0 place-items-center rounded-full text-slate-400 transition hover:bg-slate-200 hover:text-slate-700" onClick={() => setContractFile(null)}><Trash2 size={14} /></button>
                                </div>
                            )}
                            <input ref={fileRef} type="file" accept={FILE_ACCEPT} className="hidden" onChange={(event) => { setContractFile(event.target.files?.[0] || null); event.target.value = ''; }} />
                            <button type="button" className={`${iosBtnGhost} -ml-2`} onClick={() => fileRef.current?.click()}>
                                <Paperclip size={14} /> {editing?.has_file || contractFile ? 'Заменить файл' : 'Приложить файл'}
                            </button>
                        </Field>
                        <Field label="Примечание">{text('note', { maxLength: 2000 })}</Field>
                    </>
                );
            case 'cards':
                return (
                    <>
                        <Field as="div" label="Чья карта" required optionalMark={false} hint={HINTS.cardOwner}>
                            <Choice value={draft.owner_kind} options={CARD_OWNER_OPTIONS} onChange={(v) => setDraft((prev) => ({ ...prev, owner_kind: v, user_id: null, counterparty_id: null }))} ariaLabel="Чья карта" />
                        </Field>
                        {draft.owner_kind === 'employee' ? (
                            <Field label="Сотрудник" required optionalMark={false}>
                                <UserSelect users={users} value={draft.user_id} onChange={(v) => set('user_id', v)} />
                            </Field>
                        ) : (
                            <Field label="Поставщик" required optionalMark={false}>
                                <FormSelect value={draft.counterparty_id} onChange={(v) => set('counterparty_id', v)} options={counterpartyOptions} placeholder="Выберите поставщика" searchable ariaLabel="Поставщик" />
                            </Field>
                        )}
                        <Field label="ФИО владельца карты" required={draft.owner_kind === 'supplier'} optionalMark={draft.owner_kind !== 'supplier'} hint={draft.owner_kind === 'employee' ? 'Пусто — имя сотрудника.' : 'На кого оформлена карта.'}>
                            {text('holder_name', { maxLength: 200 })}
                        </Field>
                        <Field label="Номер карты" required={!draft.id} optionalMark={Boolean(draft.id)} hint={draft.id ? `Сейчас ${editing?.mask || 'номер не сохранён'}. Оставьте пустым, чтобы не менять.` : HINTS.cardNumber}>
                            <input className={`${iosInput} tabular-nums`} inputMode="numeric" autoComplete="off" value={cardNumberLabel(draft.card_number)} onChange={(event) => set('card_number', event.target.value.replace(/\D/g, '').slice(0, 19))} placeholder={draft.id ? 'Не менять' : '0000 0000 0000 0000'} />
                        </Field>
                        <Field label="Примечание">{text('note', { maxLength: 2000 })}</Field>
                    </>
                );
            case 'limits':
                return (
                    <>
                        <div className="grid gap-3 sm:grid-cols-2">
                            <Field label="Согласующий" required optionalMark={false} hint="Кому уходит заявка, подошедшая под все условия лимита.">
                                <UserSelect users={users} value={draft.delegate_user_id} onChange={(v) => set('delegate_user_id', v)} />
                            </Field>
                            <Field label="Лимит суммы, ₸" hint="Заявка на бо́льшую сумму под лимит не попадает. Пусто — без ограничения суммы.">
                                <AmountInput value={draft.amount_limit} onChange={(v) => set('amount_limit', v)} placeholder="Без лимита" ariaLabel="Лимит суммы" />
                            </Field>
                        </div>
                        <Field as="div" label="Поставщики" required optionalMark={false} hint={{ ...HINTS.limitScope, intro: 'Заявки на каких поставщиков утверждает согласующий.' }}>
                            <div className="space-y-2">
                                <Choice value={Boolean(draft.all_counterparties)} options={SCOPE_OPTIONS} onChange={(v) => set('all_counterparties', v)} ariaLabel="Поставщики лимита" />
                                {!draft.all_counterparties && (
                                    <FormSelect value={draft.counterparty_ids} onChange={(v) => set('counterparty_ids', v)} options={counterpartyOptions} placeholder="Выберите поставщиков" multiple searchable ariaLabel="Поставщики" />
                                )}
                            </div>
                        </Field>
                        {conditionFields}
                        <Field as="div" label="Проекты" required optionalMark={false} hint={{ ...HINTS.limitScope, intro: 'На расходы каких проектов распространяется лимит.' }}>
                            <div className="space-y-2">
                                <Choice value={Boolean(draft.all_projects)} options={SCOPE_OPTIONS} onChange={(v) => set('all_projects', v)} ariaLabel="Проекты лимита" />
                                {!draft.all_projects && (
                                    <FormSelect value={draft.project_ids} onChange={(v) => set('project_ids', v)} options={projectOptions} placeholder="Выберите проекты" multiple searchable ariaLabel="Проекты" />
                                )}
                            </div>
                        </Field>
                        <div className="grid gap-3 sm:grid-cols-2">
                            <Field label="Действует с">{date('starts_on', 'Начало действия', 'Сразу')}</Field>
                            <Field label="Действует до" hint="Пусто — бессрочно.">{date('ends_on', 'Окончание действия', 'Бессрочно')}</Field>
                        </div>
                        <div className="grid gap-3 sm:grid-cols-2">
                            <Field label="Номер приказа" hint="Основание лимита, если право согласования передано приказом.">{text('number', { maxLength: 100 })}</Field>
                            <Field label="Дата приказа">{date('issued_on', 'Дата приказа')}</Field>
                        </div>
                        <Field as="div" label="Статус" required optionalMark={false} hint={HINTS.limitStatus}>
                            <Choice value={draft.status} options={LIMIT_STATUS_OPTIONS} onChange={(v) => set('status', v)} ariaLabel="Статус лимита" />
                        </Field>
                        <Field label="Примечание">{text('note', { maxLength: 2000 })}</Field>
                    </>
                );
            case 'routes':
                return (
                    <>
                        <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_120px]">
                            <Field label="Название" required optionalMark={false}>{text('name', { maxLength: 200, placeholder: 'Например: пополнения карт' })}</Field>
                            <Field label="Порядок" optionalMark={false} hint="Применяется маршрут с бо́льшим числом совпавших условий; при равенстве — тот, у кого порядок меньше.">
                                <input className={`${iosInput} tabular-nums`} inputMode="numeric" value={draft.position} onChange={(event) => set('position', event.target.value.replace(/\D/g, '').slice(0, 4))} />
                            </Field>
                        </div>
                        {conditionFields}
                        <Field as="div" label="Сумма заявки, ₸" hint="Пусто — любая сумма.">
                            <div className="flex items-center gap-2">
                                <AmountInput value={draft.amount_from} onChange={(v) => set('amount_from', v)} placeholder="от" ariaLabel="Сумма от" />
                                <AmountInput value={draft.amount_to} onChange={(v) => set('amount_to', v)} placeholder="до" ariaLabel="Сумма до" />
                            </div>
                        </Field>
                        <Field as="div" label="Этап руководителя" required optionalMark={false} hint={HINTS.managerStep}>
                            <Choice value={draft.manager_step} options={MANAGER_STEP_OPTIONS} onChange={(v) => set('manager_step', v)} ariaLabel="Этап руководителя" />
                        </Field>
                        <Field label="Согласующий" hint={HINTS.routeApprover}>
                            <UserSelect users={users} value={draft.approver_user_id} onChange={(v) => set('approver_user_id', v)} placeholder="Любой из утверждающих" />
                        </Field>
                        <Field label="Примечание">{text('note', { maxLength: 2000 })}</Field>
                    </>
                );
            default:
                return <Field label="Название" required optionalMark={false}>{text('name', { maxLength: 200 })}</Field>;
        }
    };

    if (!current) return null;
    const isNew = !editing?.id;

    const row = (item, { indent = false, strong = false } = {}) => {
        const view = rowView(item);
        return (
            <div key={item.id} data-row={item.id} className="flex items-center gap-3 border-b border-slate-100 px-4 py-2.5 last:border-b-0">
                <button type="button" className={`min-w-0 flex-1 rounded-lg text-left transition hover:opacity-70 ${indent ? 'pl-5' : ''}`} onClick={() => openEditor(item)}>
                    <div className={`truncate text-[13.5px] ${strong ? 'font-medium' : ''} ${indent ? 'text-slate-700' : 'text-slate-900'}`}>{view.title}</div>
                    {view.sub && <div className="truncate text-[12px] text-slate-500">{view.sub}</div>}
                </button>
                <div className="flex shrink-0 items-center gap-2">{view.side}</div>
            </div>
        );
    };

    return (
        <div className="md:grid md:grid-cols-[224px_minmax(0,1fr)] md:items-start md:gap-4">
            <nav aria-label="Справочники" className="hidden rounded-2xl bg-slate-100/80 p-1.5 md:block">
                {sections.map((item) => (
                    <button
                        key={item.value}
                        type="button"
                        aria-current={item.value === name ? 'page' : undefined}
                        onClick={() => setTab(item.value)}
                        className={`block w-full rounded-xl px-3 py-2 text-left text-[13.5px] transition ${item.value === name ? 'bg-white font-medium text-slate-900 shadow-[0_1px_3px_rgba(15,23,42,0.10)]' : 'text-slate-600 hover:text-slate-900'}`}
                    >
                        {item.label}
                    </button>
                ))}
            </nav>
            <div className="mb-3 md:hidden">
                <CustomSelect value={name} onChange={setTab} options={sections.map((item) => ({ value: item.value, label: item.label }))} variant="ios" ariaLabel="Справочник" />
            </div>

            <div className="min-w-0 space-y-3">
                <div className="flex flex-wrap items-start justify-between gap-2">
                    <div className="min-w-0">
                        <h2 className="text-[16px] font-semibold text-slate-900">{current.label}</h2>
                        <p className="text-[12.5px] text-slate-500">{current.about}</p>
                    </div>
                    {!ownScreen && canEdit && (
                        <button type="button" className={`${iosBtnPrimary} shrink-0`} onClick={() => openEditor(null)}>
                            <Plus size={15} /> Добавить
                        </button>
                    )}
                </div>

                {name === 'settings' && (
                    <Settings apiBaseUrl={apiBaseUrl} headers={headers} showToast={showToast} onSaved={onSettingsChanged} />
                )}
                {name === 'templates' && (
                    <FixedPaymentsPanel
                        apiBaseUrl={apiBaseUrl}
                        headers={headers}
                        users={users}
                        dictionaries={dictionaries}
                        me={me}
                        showToast={showToast}
                        canEdit={Boolean(capabilities.is_admin)}
                        onChanged={onDictionariesChanged}
                        onRequestsChanged={onRequestsChanged}
                        onOpenRequest={onOpenRequest}
                    />
                )}
                {!ownScreen && (
                    <>
                        {rows.length >= SEARCH_FROM && (
                            <div className="relative">
                                <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                                <input type="search" className={`${iosInput} pl-9`} placeholder="Поиск по справочнику" aria-label="Поиск по справочнику" value={filter} onChange={(event) => setFilter(event.target.value)} />
                            </div>
                        )}
                        <div className={`${iosCard} overflow-hidden`}>
                            {loading && !rows.length && (
                                <div className="flex items-center justify-center gap-2 py-10 text-[13px] text-slate-500"><Loader2 size={15} className="animate-spin" /> Загружаем…</div>
                            )}
                            {!loading && !shown.length && (
                                <div className="px-4 py-10 text-center text-[13.5px] text-slate-500">
                                    {rows.length ? 'Ничего не нашлось' : `Пока пусто${canEdit ? ' — добавьте первую запись' : ''}`}
                                </div>
                            )}
                            {grouped
                                ? pageItems.map(({ root, children }) => (
                                    <React.Fragment key={root.id}>
                                        {row(root, { strong: true })}
                                        {children.map((child) => row(child, { indent: true }))}
                                    </React.Fragment>
                                ))
                                : pageItems.map((item) => row(item))}
                        </div>
                        <Pager page={listRange.page} pageSize={DICTIONARY_PAGE_SIZE} total={listItems.length} forms={RECORD_FORMS}
                            onPage={(next) => setListPaging({ key: listKey, page: next })} />
                    </>
                )}
            </div>

            <IosModal
                open={Boolean(editing)}
                onClose={() => setEditing(null)}
                title={`${current.label}: ${isNew ? 'новая запись' : (canEdit ? 'правка' : 'просмотр')}`}
                maxWidth="max-w-2xl"
                footer={canEdit ? (
                    <>
                        {!isNew && (
                            confirmDelete ? (
                                <div className="mr-auto flex items-center gap-2 text-[12.5px] text-rose-700">
                                    Удалить без возврата?
                                    <button type="button" className={`${iosBtnSecondary} !text-rose-700`} disabled={saving} onClick={remove}>Да, удалить</button>
                                    <button type="button" className={`${iosBtnSecondary}`} onClick={() => setConfirmDelete(false)}>Нет</button>
                                </div>
                            ) : (
                                <button type="button" className={`${iosBtnSecondary} mr-auto !text-rose-600`} disabled={saving} onClick={() => setConfirmDelete(true)}>Удалить</button>
                            )
                        )}
                        <button type="button" className={iosBtnSecondary} onClick={() => setEditing(null)} disabled={saving}>Отмена</button>
                        <button type="button" className={iosBtnPrimary} onClick={save} disabled={saving}>
                            {saving && <Loader2 size={14} className="animate-spin" />} Сохранить
                        </button>
                    </>
                ) : (
                    <button type="button" className={iosBtnPrimary} onClick={() => setEditing(null)}>Закрыть</button>
                )}
            >
                <div className="space-y-3">
                <ErrorBox text={error} />
                {/* Без права записи поля закрыты разом: fieldset гасит и поля, и кнопки-селекторы. */}
                <fieldset disabled={!canEdit} className="m-0 min-w-0 space-y-3 border-0 p-0">
                    {editing && renderForm()}
                    {editing && !isNew && WITH_ACTIVE_FLAG.includes(name) && (
                        <Field as="div" label="В новых заявках" optionalMark={false} hint={HINTS.active}>
                            <Choice value={Boolean(draft.is_active)} options={ACTIVE_OPTIONS} onChange={(v) => set('is_active', v)} ariaLabel="Активна ли запись" />
                        </Field>
                    )}
                    {editing && !isNew && name === 'routes' && (
                        <Field as="div" label="Маршрут" optionalMark={false} hint="Отключённый маршрут к заявкам не применяется.">
                            <Choice value={Boolean(draft.is_active)} options={ROUTE_ACTIVE_OPTIONS} onChange={(v) => set('is_active', v)} ariaLabel="Действует ли маршрут" />
                        </Field>
                    )}
                </fieldset>
                </div>
            </IosModal>
        </div>
    );
};

export default PaymentsDictionaries;
