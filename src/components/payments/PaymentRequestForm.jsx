import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import { ArrowRight, FileText, Loader2, Paperclip, Plus, RefreshCcw, ShoppingCart, X } from 'lucide-react';
import { iosBtnGhost, iosBtnPrimary, iosBtnSecondary, iosInput, IosModal, IosSection } from '../ui/ios';
import PaymentItemsEditor, { newItem } from './PaymentItemsEditor';
import PaymentOffersEditor, { newOffer, offerFromRow, offerPayload } from './PaymentOffersEditor';
import {
    ACCOUNTING_CATEGORY_OPTIONS, ALTERNATIVES_OPTIONS, CARD_RECIPIENT_OPTIONS, CONTRACT_THRESHOLD, HINTS, MAX_OBSERVERS,
    OBJECT_TYPE_OPTIONS, PAYMENT_METHOD_OPTIONS, PERIOD_META, UNSTAFFED, approvalSummary, cardDigits, cardLuhnOk,
    cardNumberLabel, cardProblem, fileKindOptions, fileSizeLabel, fmtDate, fmtMoney, isBlankItem, isBlankOffer,
    issueKeys, issueSentence, issuesByKey, itemQuantity, itemsTotal, legacyIssues, observersClosedLine, observersPatch,
    parseAmount, priceTrend, purchaseIssues, regularIssues, trendLabel,
} from './paymentsMeta';
import {
    AmountInput, ApprovalHint, Choice, ErrorBox, Field, FieldHint, FilePicker, FormDate, FormInput, FormSelect, FormText,
    INVALID_RING, IssueNav, NoticeBox, PeopleValue, RequisitesBox, Row, UserSelect, errorText, fromKeyboard, multipart,
    useFieldIssue, useIssueNav,
} from './paymentsUi';

/*
 * Форма подачи заявки (ТЗ «Закуп и оплата», п. 4) — создание и доработка.
 *
 * Сначала человек выбирает тип обращения: «Новый закуп» или «Оплата по
 * действующему/регулярному обязательству» — от него зависит вся форма.
 *
 * Новый закуп: что покупаем и зачем (наименование, категория, обоснование,
 * позиции с количеством и ценой, товар или услуга, срок), для кого (компания,
 * подразделение), у кого (варианты поставщиков, рекомендуемый, обоснование
 * выбора) и как платим (счёт или карта). Всё это ТЗ называет полем заявки,
 * поэтому всё обязательно; необязательное спрятано под чипы «+ …».
 *
 * Регулярный платёж: выбирается из справочника, и поставщик, договор, компания,
 * реквизиты, назначение, лимит и согласующий подставляются сами (п. 4.4) —
 * показаны строками, а не полями. Человек указывает период, сумму и счёт.
 *
 * Чего в форме нет намеренно: выбора руководителя и согласующего. Маршрут
 * определяет система (п. 6) — форма только показывает, кому заявка уйдёт.
 *
 * ЧЕГО НЕ ХВАТАЕТ — КРАСНЫМ У САМОГО ПОЛЯ (просьба владельца 14.10.2026). Кнопка
 * отправки нажимается всегда; не хватает обязательного — заявка не уходит, а
 * поля, в которых пусто, обводятся красным, под каждым красным написано, чего в
 * нём нет, и окно само подводит прокрутку к первому из них (кнопка — в подвале,
 * поля — выше). Таких полей несколько — в подвале «Не хватает: N» со стрелками
 * ⌃ ⌄: от поля к полю. Заполнили — рамка снимается сразу. До первой попытки
 * форма ничего не красит: пустая форма — ещё не ошибка. Сервер проверяет то же
 * самое (workflow.missing_for_submit) — здесь только подсказка.
 */

const HISTORY_DEBOUNCE_MS = 400;
/* Пункты счёта у заявки первой версии без способа оплаты стоят у «Как платим» — и
   говорят о том, что в этом поле можно сделать. */
const LEGACY_METHOD_ISSUE = 'выберите способ оплаты: для оплаты счёта понадобятся сам счёт, его номер и дата';
const TEMPLATE_ENTITY_ISSUE = 'в справочнике у платежа не указана компания — попросите администратора дополнить';
const ENTITY_UNKNOWN = 'Не знаю — уточнит бухгалтерия';
const PREVIEW_DEBOUNCE_MS = 350;
const NEW_CARD = '__new__';

const EXTRA_FIELDS = [
    { key: 'project_id', label: 'Проект' },
    { key: 'branch', label: 'Филиал / регион' },
    { key: 'notes', label: 'Комментарий' },
    { key: 'files', label: 'Вложения' },
    { key: 'observer_ids', label: 'Наблюдатели' },
];
/* У регулярного платежа комментарий стоит открытым в «Оплате», остального нет вовсе —
   под чипом только наблюдатели. */
const REGULAR_EXTRA_FIELDS = EXTRA_FIELDS.filter((field) => field.key === 'observer_ids');
// Поле заполнено — его чип раскрыт: пустой список наблюдателей — то же, что пустое поле.
const hasValue = (value) => (Array.isArray(value) ? value.length > 0 : Boolean(value));

// Количество из базы («5.000») — в том виде, как его набирает человек («5», «2,5»).
const quantityInput = (value) => {
    const number = parseAmount(value);
    return number ? String(number).replace('.', ',') : '1';
};

const emptyDraft = (me, kind = null) => ({
    request_kind: kind,
    legal_entity_id: null, department_id: me?.department_id || null, project_id: null, branch: '',
    expense_name: '', justification: '', category_id: null, subcategory_id: null, due_on: '',
    object_type: null, accounting_category: null, payment_method: null,
    items: [newItem()],
    offers: [newOffer(), newOffer(), newOffer()],
    no_alternatives: false, no_alternatives_reason: null, no_alternatives_comment: '',
    supplier_choice_reason: '',
    contract_id: null, counterparty_account_id: null, invoice_number: '', invoice_date: '',
    card_recipient: null, card_holder_user_id: me?.id || null, card_holder_name: '', card_id: null,
    card_number: '', payment_purpose: '', notes: '',
    fixed_template_id: null, amount: 0, payment_period: '',
    // Наблюдатели — номерами строками, как их отдаёт список выбора нескольких сотрудников.
    observer_ids: [],
});

const draftFromData = (data, me) => {
    const request = data.request;
    const base = emptyDraft(me, request.request_kind);
    const items = (data.items || []).map((item) => ({
        key: Math.random().toString(36).slice(2, 9), name: item.name || '', quantity: quantityInput(item.quantity),
        unit: item.unit || '', unit_price: parseAmount(item.unit_price),
    }));
    let offers = (data.offers || []).map(offerFromRow);
    // У заявки первой версии процесса вариантов поставщиков не было — поставщик записан
    // в самой заявке. Показываем его рекомендуемым вариантом: иначе в форме его не
    // видно вовсе, а сохранение ушло бы с пустым списком.
    if (!offers.length && request.counterparty_id) {
        offers = [newOffer({ counterparty_id: request.counterparty_id, amount: parseAmount(request.amount), is_recommended: true })];
    }
    return {
        ...base,
        legal_entity_id: request.legal_entity_id ?? null,
        department_id: request.department_id ?? null,
        project_id: request.project_id ?? null,
        branch: request.branch || '',
        expense_name: request.expense_name || '',
        justification: request.justification || '',
        category_id: request.category_id ?? null,
        subcategory_id: request.subcategory_id ?? null,
        due_on: request.due_on ? String(request.due_on).slice(0, 10) : '',
        object_type: request.object_type ?? null,
        accounting_category: request.accounting_category ?? null,
        payment_method: request.payment_method ?? null,
        items: items.length ? items : [newItem()],
        offers: offers.length ? offers : [newOffer()],
        no_alternatives: Boolean(request.no_alternatives),
        no_alternatives_reason: request.no_alternatives_reason ?? null,
        no_alternatives_comment: request.no_alternatives_comment || '',
        supplier_choice_reason: request.supplier_choice_reason || '',
        contract_id: request.contract_id ?? null,
        counterparty_account_id: request.counterparty_account_id ?? null,
        invoice_number: request.invoice_number || '',
        invoice_date: request.invoice_date ? String(request.invoice_date).slice(0, 10) : '',
        card_recipient: request.card_recipient ?? null,
        card_holder_user_id: request.card_holder_user_id ?? null,
        card_holder_name: request.card_holder_name || '',
        card_id: request.card_id ?? null,
        payment_purpose: request.payment_purpose || '',
        notes: request.notes || '',
        fixed_template_id: request.fixed_template_id ?? null,
        amount: parseAmount(request.amount),
        payment_period: request.payment_period || '',
        // Уволенного в списке выбора нет (его не снять и не увидеть) — в форму он не попадает,
        // а разница, которую форма шлёт, его не касается: снимают его в окне заявки.
        observer_ids: (data.observers || []).filter((person) => !person.fired).map((person) => String(person.user_id)),
    };
};

/* Один файл счёта: выбранный сейчас либо уже лежащий в заявке. Выбранный новый
   файл ЗАМЕНЯЕТ прежние (сервер снимает их при сохранении): у бухгалтерии не
   должно остаться двух счетов — нечитаемого и нового. Поэтому прежний файл,
   пока выбран новый, показан зачёркнутым. */
const InvoiceFile = ({ file, saved, onPick, onClear }) => {
    const inputRef = useRef(null);
    // Счёт обязателен, а его нет — кнопка «Приложить счёт» в красной рамке, как у FilePicker.
    const bad = useFieldIssue();
    return (
        <div className="flex flex-wrap items-center gap-1.5">
            {(saved || []).map((item) => (
                <span key={item.id} className={`inline-flex max-w-[260px] items-center gap-1.5 rounded-full bg-slate-100 px-2.5 py-1 text-[12.5px] ${file ? 'text-slate-400 line-through' : 'text-slate-700'}`} title={file ? `${item.file_name} — будет заменён` : item.file_name}>
                    <FileText size={12} className="shrink-0 text-slate-400" /><span className="truncate">{item.file_name}</span>
                </span>
            ))}
            {file && (
                <span className="inline-flex max-w-[300px] items-center gap-1.5 rounded-full bg-slate-100 py-1 pl-2.5 pr-1 text-[12.5px] text-slate-700">
                    <FileText size={12} className="shrink-0 text-slate-400" />
                    <span className="truncate">{file.name}</span>
                    <span className="shrink-0 text-slate-400">{fileSizeLabel(file.size)}</span>
                    <button type="button" aria-label="Убрать файл счёта" className="grid h-5 w-5 shrink-0 place-items-center rounded-full text-slate-400 transition hover:bg-slate-200 hover:text-slate-700" onClick={onClear}>
                        <X size={11} />
                    </button>
                </span>
            )}
            <input ref={inputRef} type="file" accept=".pdf,.jpg,.jpeg,.png,.webp,.heic,.doc,.docx,.xls,.xlsx" className="hidden" onChange={(event) => { onPick(event.target.files?.[0] || null); event.target.value = ''; }} />
            <button type="button" aria-invalid={bad || undefined} className={`${iosBtnGhost} -ml-1${bad ? ` !text-rose-600 ${INVALID_RING}` : ''}`} onClick={() => inputRef.current?.click()}>
                <Paperclip size={14} /> {file || (saved || []).length ? 'Заменить файл счёта' : 'Приложить счёт'}
            </button>
        </div>
    );
};

const KIND_CARDS = [
    { value: 'purchase', icon: ShoppingCart, title: 'Новый закуп',
        text: 'Покупаем впервые или у нового поставщика. Понадобятся варианты поставщиков и обоснование.' },
    { value: 'regular', icon: RefreshCcw, title: 'Оплата по действующему/регулярному обязательству',
        text: 'Аренда, связь, интернет, лицензии. Поставщик и условия уже утверждены — сравнивать не нужно.' },
];

/* Черновик без того, что меняется само (ключи строк) и что не данные (тип
   обращения выбран, но ещё ничего не введено): по нему видно, тронута ли форма. */
const fingerprint = (draft) => JSON.stringify({ ...draft, request_kind: null });

/* revealIssues — окно открыли, чтобы дописать недостающее (кнопка «Доработать заявку»
   в задаче инициатора, над ней уже названо, чего не хватает): поля красные сразу, и
   окно подводит к первому, не дожидаясь нажатия «Отправить». */
const PaymentRequestForm = ({
    open, onClose, apiBaseUrl, headers, dictionaries, users, me, meta, settings, staffed, data = null, onSaved, showToast,
    revealIssues = false,
}) => {
    const editing = Boolean(data?.request);
    const request = data?.request || null;
    // Заявка принята ещё первой версией процесса: новых требований к полноте к ней нет.
    const legacy = Number(request?.legacy_step) >= 2;
    const approvedBefore = Boolean(request?.approved_at);
    const [draft, setDraft] = useState(() => emptyDraft(me));
    const [extras, setExtras] = useState([]);
    const [files, setFiles] = useState([]);
    const [invoiceFile, setInvoiceFile] = useState(null);
    const [invoiceOpen, setInvoiceOpen] = useState(false);
    const [saving, setSaving] = useState('');       // '' | 'save' | 'submit'
    const [error, setError] = useState('');
    const [history, setHistory] = useState([]);
    const [preview, setPreview] = useState(null);
    const [cardOptions, setCardOptions] = useState([]);
    const [cardEditing, setCardEditing] = useState(false);
    const [confirmClose, setConfirmClose] = useState(false);
    /* Переход «выбор типа → форма» — как второй уровень внутри окна на сайте
       (animate-push-in / animate-pop-in): вперёд форма въезжает справа, по
       «назад» выбор типа возвращается слева. Новое окно начинает без сдвига —
       у него своё появление. */
    const [kindMotion, setKindMotion] = useState('');
    // Пытались отправить, а чего-то не хватало: с этого момента поля с пунктами красные.
    const [reveal, setReveal] = useState(false);
    const errorRef = useRef(null);
    const formRef = useRef(null);
    const pristineRef = useRef('');
    // Наблюдатели, с которыми окно открыли: форма шлёт разницу с ними, а не список целиком.
    const observersAtOpen = useRef([]);

    const toastRef = useRef(showToast);
    useEffect(() => { toastRef.current = showToast; }, [showToast]);

    // Кнопка отправки — в подвале, а ошибка — над формой: без прокрутки
    // человек жмёт «Отправить», и ничего видимого не происходит.
    useEffect(() => {
        if (error) errorRef.current?.scrollIntoView({ block: 'start', behavior: 'smooth' });
    }, [error]);

    // Форма заполняется заново только при открытии и при смене заявки. `data` и `me`
    // родитель пересоздаёт при каждом обновлении раздела (счётчики, колокол) — следи
    // форма за ними, фоновое обновление стирало бы всё, что человек успел ввести.
    //
    // Заполняется ПРИ ОТРИСОВКЕ, а не эффектом после неё: эффект отставал на кадр, и
    // этот кадр окно стояло с тем, что осталось от прошлого раза, — после созданной
    // заявки «Создать заявку» мелькало прежней формой во весь рост вместо двух плиток
    // выбора. React перерисовывает компонент с новым состоянием ещё до показа кадра.
    const requestId = data?.request?.id || null;
    const fillKey = open ? String(requestId ?? 'new') : null;
    const [filledFor, setFilledFor] = useState(null);
    if (fillKey !== filledFor) {
        setFilledFor(fillKey);
        if (fillKey !== null) {
            setError('');
            setFiles([]);
            setInvoiceFile(null);
            setPreview(null);
            setCardEditing(false);
            setConfirmClose(false);
            setReveal(Boolean(revealIssues && data?.request));
            // Новое окно начинает без сдвига содержимого — у него своё появление.
            setKindMotion('');
            let next = emptyDraft(me);
            if (data?.request) {
                next = draftFromData(data, me);
                setExtras(EXTRA_FIELDS.map((field) => field.key).filter((key) => hasValue(next[key])));
                setInvoiceOpen(Boolean(next.invoice_number || next.invoice_date
                    || (data.attachments || []).some((item) => item.kind === 'invoice')
                    || data.request.clarify_reason === 'invoice_missing'));
            } else {
                setExtras([]);
                setInvoiceOpen(false);
            }
            setDraft(next);
            pristineRef.current = fingerprint(next);
            observersAtOpen.current = next.observer_ids;
        }
    }

    const set = useCallback((key, value) => setDraft((prev) => ({ ...prev, [key]: value })), []);
    const kind = draft.request_kind;
    const regular = kind === 'regular';

    // ── Справочники ──
    const categories = useMemo(() => (dictionaries?.categories || []).filter((item) => !item.parent_id), [dictionaries]);
    const subcategories = useMemo(() => (dictionaries?.categories || [])
        .filter((item) => item.parent_id && item.parent_id === draft.category_id), [dictionaries, draft.category_id]);
    const counterpartyOptions = useMemo(() => (dictionaries?.counterparties || [])
        .map((item) => ({ value: item.id, label: item.bin ? `${item.name} · БИН ${item.bin}` : item.name })), [dictionaries]);
    const projectOptions = useMemo(() => [{ value: null, label: 'Без проекта' },
        ...(dictionaries?.projects || []).map((item) => ({ value: item.id, label: item.name }))], [dictionaries]);
    const legalEntities = dictionaries?.legal_entities || [];
    const legalEntityOptions = useMemo(() => legalEntities.map((item) => ({ value: item.id, label: item.name })), [legalEntities]);
    /* Компания нового закупа необязательна (просьба владельца 15.10.2026): не знает её
       инициатор — после отправки её укажет бухгалтерия. «Не знаю» стоит первым пунктом
       списка и подсказкой пустого поля — как «Без проекта» у проекта. У заявки первой
       версии компания по-прежнему нужна: она уже согласована и ждёт оплаты. */
    const entityOptional = kind === 'purchase' && !legacy;
    const entityChoiceOptions = useMemo(() => (entityOptional && legalEntityOptions.length
        ? [{ value: null, label: ENTITY_UNKNOWN }, ...legalEntityOptions] : legalEntityOptions), [entityOptional, legalEntityOptions]);
    const legalEntity = legalEntities.find((item) => item.id === draft.legal_entity_id) || null;
    const departmentOptions = useMemo(() => (dictionaries?.departments || [])
        .map((item) => ({ value: item.id, label: item.name })), [dictionaries]);
    const templates = dictionaries?.templates || [];
    const template = templates.find((item) => item.id === draft.fixed_template_id) || null;
    const reasonOptions = useMemo(() => (meta?.no_alternatives_reasons || [])
        .map((item) => ({ value: item.code, label: item.label })), [meta]);

    // ── Производные ──
    const total = useMemo(() => (regular ? parseAmount(draft.amount) : itemsTotal(draft.items)), [regular, draft.amount, draft.items]);
    const offers = useMemo(() => (draft.no_alternatives ? draft.offers.slice(0, 1) : draft.offers), [draft.no_alternatives, draft.offers]);
    const recommended = useMemo(() => (draft.no_alternatives
        ? offers[0] : offers.find((offer) => offer.is_recommended)) || null, [draft.no_alternatives, offers]);
    const supplierId = regular ? (template?.counterparty_id || null)
        : (recommended && !recommended.custom ? recommended.counterparty_id : null);
    const contractOptions = useMemo(() => (dictionaries?.contracts || [])
        .filter((item) => item.counterparty_id === supplierId)
        .map((item) => ({
            value: item.id,
            label: `№${item.number}${item.ends_on ? ` до ${fmtDate(item.ends_on)}` : ''}${item.status !== 'active' ? ' · недействующий' : ''}`,
        })), [dictionaries, supplierId]);
    const accountOptions = useMemo(() => (dictionaries?.counterparty_accounts || [])
        .filter((item) => item.counterparty_id === supplierId)
        .map((item) => ({ value: item.id, label: item.text })), [dictionaries, supplierId]);
    const savedFilesByOffer = useMemo(() => {
        const map = {};
        (data?.attachments || []).forEach((item) => { if (item.offer_id) (map[item.offer_id] ||= []).push(item); });
        return map;
    }, [data]);
    const savedInvoices = useMemo(() => (data?.attachments || []).filter((item) => item.kind === 'invoice'), [data]);
    const fileKinds = useMemo(() => [
        ...(data?.attachments || []).map((item) => item.kind),
        ...files.map((entry) => entry.kind),
        ...(invoiceFile ? ['invoice'] : []),
    ], [data, files, invoiceFile]);
    const threshold = meta?.contract_threshold || CONTRACT_THRESHOLD;
    // Договор и реквизиты относятся к поставщику: сменили рекомендуемого — выбранные
    // для прежнего больше не действуют (сервер отказал бы: «договор заключён с другим»).
    const contractId = contractOptions.some((option) => option.value === draft.contract_id) ? draft.contract_id : null;
    const accountId = accountOptions.some((option) => option.value === draft.counterparty_account_id)
        ? draft.counterparty_account_id : null;
    const contractNeeded = draft.payment_method === 'invoice' && total > threshold && !contractId;
    const trend = useMemo(() => priceTrend(history), [history]);
    // Сохранённый номер карты действует, пока получатель прежний. Сменили получателя,
    // сотрудника или поставщика — это уже карта другого человека: номер вводится заново.
    const holderChanged = Boolean(editing && request?.has_card && (
        draft.card_recipient !== (request.card_recipient ?? null)
        || (draft.card_recipient === 'employee' && draft.card_holder_user_id !== (request.card_holder_user_id ?? null))
        || (draft.card_recipient === 'supplier' && supplierId !== (request.counterparty_id ?? null))));
    const cardKept = Boolean(editing && request?.has_card && !cardEditing && !holderChanged);

    /* Чего не хватает — пунктами с ключом поля (paymentsMeta.purchaseIssues). Пункт,
       чьего поля сейчас нет на экране, стоит у того, что надо сделать раньше, и
       говорит о том, что можно сделать там: счёт старой заявки без способа оплаты — у
       «Как платим», компания регулярного платежа — у выбора платежа (её задаёт
       справочник). Номер карты до выбора получателя не показан вовсе: его поле
       появится вместе с выбором (уже красным, если попытка была). */
    const issues = useMemo(() => {
        if (!kind) return [];
        let list;
        if (legacy) list = legacyIssues({ ...draft, offers }, { fileKinds });
        else if (regular) list = regularIssues(draft, { fileKinds, template, staffed });
        else {
            list = purchaseIssues({ ...draft, offers }, {
                minSuppliers: settings?.min_suppliers ?? 3, fileKinds, hasCard: cardKept, staffed,
            });
        }
        const placed = [];
        list.forEach((item) => {
            if (item.key === 'card_number' && !draft.card_recipient) return;
            if (item.key.startsWith('invoice_') && !regular && draft.payment_method !== 'invoice') {
                if (!placed.some((other) => other.text === LEGACY_METHOD_ISSUE)) placed.push({ key: 'payment_method', text: LEGACY_METHOD_ISSUE });
                return;
            }
            if (item.key === 'legal_entity_id' && regular) {
                placed.push({ key: 'fixed_template_id', text: TEMPLATE_ENTITY_ISSUE });
                return;
            }
            placed.push(item);
        });
        return placed;
    }, [kind, legacy, regular, draft, offers, fileKinds, settings, cardKept, template, staffed]);
    const byKey = useMemo(() => issuesByKey(issues), [issues]);
    const keys = useMemo(() => issueKeys(issues), [issues]);
    const nav = useIssueNav(formRef, keys);
    // Что написать под полем; пока не пытались отправить — ничего.
    const issueOf = (key) => (reveal && byKey[key] ? issueSentence(byKey[key].map((item) => item.text)) : '');

    /* Окно открыли дописывать недостающее — сразу к первому полю, где пусто. Один раз
       на открытие: дальше человек ходит по полям сам. */
    const revealedFor = useRef(null);
    useEffect(() => {
        if (!open) { revealedFor.current = null; return; }
        if (!revealIssues || !reveal || !keys.length || revealedFor.current === fillKey) return;
        revealedFor.current = fillKey;
        if (draft.payment_method === 'invoice' && keys.some((key) => key.startsWith('invoice_'))) setInvoiceOpen(true);
        nav.first();
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [open, reveal, fillKey, revealIssues]);

    /* Пункты счёта появились, когда поля уже красные (у старой заявки переключили
       «Как платим» на счёт), — блок счёта раскрывается сам: иначе пункты стояли бы у
       полей, которых на экране нет. */
    useEffect(() => {
        if (reveal && !invoiceOpen && draft.payment_method === 'invoice' && keys.some((key) => key.startsWith('invoice_'))) setInvoiceOpen(true);
    }, [reveal, invoiceOpen, draft.payment_method, keys]);

    // ── Справка «История оплат»: похожие ранее оплаченные заявки ──
    useEffect(() => {
        if (!open || !kind) return undefined;
        if (!supplierId && !draft.category_id && (draft.expense_name || '').trim().length < 3) {
            setHistory([]);
            return undefined;
        }
        // cancelled: ответ на прежние условия не должен затереть ответ на новые.
        let cancelled = false;
        const timer = setTimeout(() => {
            const params = new URLSearchParams();
            if (supplierId) params.set('counterparty_id', supplierId);
            if (draft.category_id) params.set('category_id', draft.category_id);
            if (draft.subcategory_id) params.set('subcategory_id', draft.subcategory_id);
            if ((draft.expense_name || '').trim().length >= 3) params.set('name', draft.expense_name.trim());
            if (request?.id) params.set('exclude', request.id);
            axios.get(`${apiBaseUrl}/api/payments/history?${params}`, { headers: headers() })
                .then((response) => { if (!cancelled) setHistory(response.data?.items || []); })
                .catch(() => { if (!cancelled) setHistory([]); });
        }, HISTORY_DEBOUNCE_MS);
        return () => { cancelled = true; clearTimeout(timer); };
    }, [open, kind, apiBaseUrl, headers, supplierId, draft.category_id, draft.subcategory_id, draft.expense_name, request?.id]);

    // ── Кому уйдёт заявка: маршрут определяет система (п. 6) ──
    useEffect(() => {
        if (!open || !kind || !(total > 0)) { setPreview(null); return undefined; }
        let cancelled = false;
        const timer = setTimeout(() => {
            const params = new URLSearchParams({ amount: String(total), request_kind: kind });
            if (supplierId) params.set('counterparty_id', supplierId);
            const entityId = regular ? template?.legal_entity_id : draft.legal_entity_id;
            if (entityId) params.set('legal_entity_id', entityId);
            if (draft.department_id) params.set('department_id', draft.department_id);
            const categoryId = regular ? template?.category_id : draft.category_id;
            if (categoryId) params.set('category_id', categoryId);
            if (draft.project_id) params.set('project_id', draft.project_id);
            const method = regular ? 'invoice' : draft.payment_method;
            if (method) params.set('payment_method', method);
            if (regular && draft.fixed_template_id) params.set('fixed_template_id', draft.fixed_template_id);
            if (request?.initiator_id) params.set('initiator_id', request.initiator_id);
            axios.get(`${apiBaseUrl}/api/payments/route-preview?${params}`, { headers: headers() })
                .then((response) => { if (!cancelled) setPreview(response.data || null); })
                .catch(() => { if (!cancelled) setPreview(null); });
        }, PREVIEW_DEBOUNCE_MS);
        return () => { cancelled = true; clearTimeout(timer); };
    }, [open, kind, regular, total, supplierId, template, draft.legal_entity_id, draft.department_id, draft.category_id,
        draft.project_id, draft.payment_method, draft.fixed_template_id, request?.initiator_id, apiBaseUrl, headers]);

    // ── Сохранённые карты получателя (справочник карт, только маски) ──
    const cardOwnerUser = draft.card_recipient === 'employee' ? draft.card_holder_user_id : null;
    const cardOwnerSupplier = draft.card_recipient === 'supplier' ? supplierId : null;
    useEffect(() => {
        if (!open || draft.payment_method !== 'card' || !(cardOwnerUser || cardOwnerSupplier)) {
            setCardOptions([]);
            return undefined;
        }
        let cancelled = false;
        const params = new URLSearchParams({ owner_kind: draft.card_recipient });
        if (cardOwnerUser) params.set('user_id', cardOwnerUser);
        if (cardOwnerSupplier) params.set('counterparty_id', cardOwnerSupplier);
        axios.get(`${apiBaseUrl}/api/payments/cards/options?${params}`, { headers: headers() })
            .then((response) => { if (!cancelled) setCardOptions(response.data?.items || []); })
            .catch(() => { if (!cancelled) setCardOptions([]); });
        return () => { cancelled = true; };
    }, [open, draft.payment_method, draft.card_recipient, cardOwnerUser, cardOwnerSupplier, apiBaseUrl, headers]);

    const addExtra = (key) => setExtras((prev) => (prev.includes(key) ? prev : [...prev, key]));
    const removeExtra = (key) => {
        setExtras((prev) => prev.filter((item) => item !== key));
        if (key === 'files') setFiles([]);
        else if (key === 'observer_ids') set('observer_ids', []);
        else set(key, key.endsWith('_id') ? null : '');
    };

    /* Наблюдатели: не сам инициатор (он и так видит свою заявку), не больше общего предела.
       Кому раздел не открыт, тот заявку не увидит, — форма говорит об этом сразу, при выборе. */
    const initiatorId = editing ? request.initiator_id : me?.id;
    const observerExclude = useMemo(() => (initiatorId ? [initiatorId] : []), [initiatorId]);
    const maxObservers = Number(meta?.max_observers) || MAX_OBSERVERS;
    const observersClosed = useMemo(() => {
        const picked = new Set((draft.observer_ids || []).map(String));
        return (users || []).filter((user) => picked.has(String(user.id)) && user.section_open === false)
            .map((user) => user.name);
    }, [users, draft.observer_ids]);
    // Наблюдатели уходят разницей с тем, что было при открытии формы (paymentsMeta.observersPatch).
    const observersPayload = () => observersPatch(draft.observer_ids, observersAtOpen.current);

    const pickTemplate = (id) => {
        const next = templates.find((item) => item.id === id) || null;
        setDraft((prev) => ({
            ...prev,
            fixed_template_id: id,
            amount: next ? parseAmount(next.amount) : prev.amount,
            due_on: next?.next_due_on ? String(next.next_due_on).slice(0, 10) : prev.due_on,
        }));
    };

    const payloadOf = (submit) => {
        if (regular) {
            return {
                request_kind: 'regular', fixed_template_id: draft.fixed_template_id, amount: parseAmount(draft.amount),
                payment_period: draft.payment_period.trim(), department_id: draft.department_id,
                due_on: draft.due_on || null, invoice_number: draft.invoice_number.trim(),
                invoice_date: draft.invoice_date || null, notes: draft.notes.trim(),
                replace_invoice: Boolean(invoiceFile), submit,
                ...observersPayload(),
            };
        }
        const sent = offers.filter((offer) => !isBlankOffer(offer));
        const card = draft.payment_method === 'card' && !cardKept
            ? (draft.card_id ? { card_id: draft.card_id } : { card_number: cardDigits(draft.card_number) })
            : {};
        return {
            request_kind: 'purchase',
            legal_entity_id: draft.legal_entity_id, department_id: draft.department_id, project_id: draft.project_id,
            branch: draft.branch.trim(), expense_name: draft.expense_name.trim(), justification: draft.justification.trim(),
            category_id: draft.category_id, subcategory_id: draft.subcategory_id, due_on: draft.due_on || null,
            object_type: draft.object_type, accounting_category: draft.accounting_category,
            payment_method: draft.payment_method,
            // Те же строки, что посчитаны в сумме на экране: пустые не уходят.
            items: draft.items.filter((item) => !isBlankItem(item)).map((item) => ({
                name: item.name.trim(), quantity: itemQuantity(item), unit: item.unit || null,
                unit_price: parseAmount(item.unit_price),
            })),
            offers: sent.map((offer, index) => ({
                ...offerPayload(offer),
                is_recommended: draft.no_alternatives ? index === 0 : Boolean(offer.is_recommended),
            })),
            no_alternatives: Boolean(draft.no_alternatives),
            no_alternatives_reason: draft.no_alternatives ? draft.no_alternatives_reason : null,
            no_alternatives_comment: draft.no_alternatives ? draft.no_alternatives_comment.trim() : '',
            supplier_choice_reason: draft.supplier_choice_reason.trim(),
            contract_id: contractId, counterparty_account_id: accountId,
            invoice_number: draft.invoice_number.trim(), invoice_date: draft.invoice_date || null,
            replace_invoice: Boolean(invoiceFile),
            card_recipient: draft.payment_method === 'card' ? draft.card_recipient : null,
            card_holder_user_id: draft.card_recipient === 'employee' ? draft.card_holder_user_id : null,
            card_holder_name: draft.card_recipient === 'supplier' ? draft.card_holder_name.trim() : '',
            payment_purpose: draft.payment_purpose.trim(), notes: draft.notes.trim(),
            ...card,
            ...observersPayload(),
            submit,
        };
    };

    const filesOf = () => {
        const list = [];
        if (!regular) {
            offers.filter((offer) => !isBlankOffer(offer)).forEach((offer, index) => {
                if (offer.file) list.push({ file: offer.file, kind: 'offer', offerIndex: index });
            });
        }
        if (invoiceFile) list.push({ file: invoiceFile, kind: 'invoice' });
        return [...list, ...files];
    };

    /* «Отправить»: не хватает обязательного — заявка не уходит, поля краснеют, и окно
       подводит к первому из них (после отрисовки: блок счёта, может быть, ещё свёрнут).
       Нажали с клавиатуры — фокус переходит в это поле. */
    const trySubmit = (event) => {
        if (saving) return;
        if (issues.length) {
            if (draft.payment_method === 'invoice' && keys.some((key) => key.startsWith('invoice_'))) setInvoiceOpen(true);
            setReveal(true);
            nav.first({ focus: fromKeyboard(event) });
            return;
        }
        save(true);
    };

    const save = async (submit) => {
        setSaving(submit ? 'submit' : 'save');
        setError('');
        try {
            const form = multipart(payloadOf(submit), filesOf());
            const response = editing
                ? await axios.patch(`${apiBaseUrl}/api/payments/requests/${request.id}`, form, { headers: headers() })
                : await axios.post(`${apiBaseUrl}/api/payments/requests`, form, { headers: headers() });
            const body = response.data || {};
            if (body.warnings?.length) toastRef.current?.(body.warnings.join('. '), 'info');
            let text = 'Заявка сохранена';
            if (body.submitted) text = body.rerouted ? 'Условия изменились — заявка ушла на согласование заново' : 'Заявка отправлена';
            // Компанию не указали — заявка сначала у бухгалтерии, а не у согласующего.
            if (body.submitted && body.request?.current_kind === 'entity_clarify') text = 'Заявка отправлена — компанию уточнит бухгалтерия';
            toastRef.current?.(text, 'success');
            onSaved?.(body);
            onClose?.();
        } catch (err) {
            setError(errorText(err, 'Не удалось сохранить заявку'));
        } finally {
            setSaving('');
        }
    };

    // Закрыть окно с введённым можно только осознанно: щелчок мимо окна и системное
    // «назад» на телефоне иначе стирали заполненную заявку без вопроса.
    const touched = () => fingerprint(draft) !== pristineRef.current || files.length > 0 || Boolean(invoiceFile);
    const requestClose = () => {
        if (saving) return;
        if (!confirmClose && touched()) { setConfirmClose(true); return; }
        onClose?.();
    };

    const route = approvalSummary(preview?.route);
    const entityStep = entityOptional && Boolean(preview?.entity_step);
    const routeManager = preview?.manager?.name || '';
    const initiatorName = (editing ? request.initiator_name : me?.name) || '';
    // Руководитель: у своей новой заявки — из справочного набора, у чужой на доработке — записанный в заявке.
    const managerName = (editing ? request.manager_name : dictionaries?.manager?.name) || routeManager;
    const busy = Boolean(saving);
    const lunaWarn = !draft.card_id && cardDigits(draft.card_number).length >= 13 && !cardProblem(draft.card_number, meta?.card_digits)
        && !cardLuhnOk(draft.card_number);
    const cardSelect = cardOptions.length > 0;

    /* «Дополнительно»: необязательное спрятано под чипы «+ …» и раскрывается полем по
       нажатию. У нового закупа — проект, филиал, комментарий, вложения и наблюдатели; у
       регулярного платежа — только наблюдатели. */
    const extrasSection = (fields) => (
        <IosSection title="Дополнительно">
            {extras.filter((key) => fields.some((field) => field.key === key)).map((key) => {
                const meta_ = fields.find((field) => field.key === key);
                return (
                    // grow basis-0, а не flex-1: по нему оболочка телефона ставит ряд столбиком.
                    <div key={key} className="flex items-start gap-2">
                        <div className="min-w-0 grow basis-0">
                            {key === 'project_id' && (
                                <Field label={meta_.label} hint={HINTS.project}>
                                    <FormSelect value={draft.project_id} onChange={(value) => set('project_id', value)} options={projectOptions} placeholder="Без проекта" ariaLabel="Проект" searchable={projectOptions.length > 8} />
                                </Field>
                            )}
                            {key === 'branch' && (
                                <Field label={meta_.label} hint="Таксопарк (город), филиал или регион — в зависимости от проекта.">
                                    <input className={iosInput} value={draft.branch} onChange={(event) => set('branch', event.target.value)} maxLength={200} placeholder="Например: Алматы" />
                                </Field>
                            )}
                            {key === 'notes' && (
                                <Field label={meta_.label}>
                                    <textarea className={`${iosInput} min-h-[56px] resize-y`} value={draft.notes} onChange={(event) => set('notes', event.target.value)} maxLength={4000} />
                                </Field>
                            )}
                            {key === 'files' && (
                                <Field as="div" label={meta_.label} hint="Любые документы к закупу. Коммерческое предложение удобнее приложить к своему поставщику выше.">
                                    <FilePicker files={files} onChange={setFiles} kinds={fileKindOptions('request').filter((option) => option.value !== 'invoice')} />
                                </Field>
                            )}
                            {key === 'observer_ids' && (
                                <Field label={meta_.label} hint={HINTS.observers}>
                                    <UserSelect
                                        multiple
                                        users={users}
                                        value={draft.observer_ids}
                                        onChange={(ids) => set('observer_ids', ids)}
                                        exclude={observerExclude}
                                        maxSelected={maxObservers}
                                        renderValue={(ids) => <PeopleValue ids={ids} users={users} />}
                                        placeholder="Кто будет следить за заявкой"
                                    />
                                    {observersClosed.length > 0 && (
                                        <span className="block px-1 text-[12.5px] text-amber-700">
                                            {observersClosedLine(observersClosed, (draft.observer_ids || []).length)}
                                        </span>
                                    )}
                                </Field>
                            )}
                        </div>
                        {/* У наблюдателей, которые уже были у заявки, «Убрать поле» нет: крестик читается как
                            «спрятать поле», а снял бы всех разом (разбор 15.10.2026). Снимают по одному — в списке. */}
                        {!(key === 'observer_ids' && observersAtOpen.current.length > 0) && (
                            <button type="button" aria-label={`Убрать поле ${meta_.label}`} className="mt-6 grid h-8 w-8 shrink-0 place-items-center rounded-full text-slate-400 transition hover:bg-slate-100 hover:text-slate-700" onClick={() => removeExtra(key)}>
                                <X size={14} />
                            </button>
                        )}
                    </div>
                );
            })}
            <div className="flex flex-wrap gap-1.5">
                {fields.filter((field) => !extras.includes(field.key)).map((field) => (
                    <button key={field.key} type="button" onClick={() => addExtra(field.key)} className="inline-flex items-center gap-1 rounded-full bg-slate-100 px-2.5 py-1 text-[12.5px] text-slate-600 transition hover:bg-slate-200 active:scale-[0.98]">
                        <Plus size={12} /> {field.label}
                    </button>
                ))}
            </div>
        </IosSection>
    );

    let footer = <button type="button" className={iosBtnSecondary} onClick={requestClose}>Отмена</button>;
    if (confirmClose) {
        footer = (
            <>
                <span className="mr-auto text-[13px] text-slate-600 motion-safe:animate-reveal">Закрыть без сохранения? Введённое пропадёт.</span>
                <button type="button" className={iosBtnSecondary} onClick={() => setConfirmClose(false)}>Вернуться</button>
                <button type="button" className={`${iosBtnSecondary} !text-rose-600`} onClick={onClose}>Закрыть</button>
            </>
        );
    } else if (kind) {
        footer = (
            <>
                {/* Полей, где не хватает, несколько — стрелки ведут от одного к другому. Стоят слева;
                    уже 768 px (там окно уже своего предела) — своей строкой над кнопками: в одном
                    ряду с тремя кнопками они вытесняли «Отправить» отдельной строкой. */}
                {reveal && keys.length > 1 && (
                    <div className="mr-auto max-md:order-first max-md:mr-0 max-md:w-full">
                        <IssueNav count={keys.length} onStep={nav.step} />
                    </div>
                )}
                <button type="button" className={iosBtnSecondary} onClick={requestClose} disabled={busy}>Отмена</button>
                {/* Черновик сохраняется и у новой заявки: недозаполненное не должно пропадать. */}
                <button type="button" className={iosBtnSecondary} onClick={() => save(false)} disabled={busy}>
                    {saving === 'save' && <Loader2 size={14} className="animate-spin" />} {editing ? 'Сохранить' : 'Сохранить черновик'}
                </button>
                <button type="button" className={iosBtnPrimary} onClick={trySubmit} disabled={busy}>
                    {saving === 'submit' && <Loader2 size={14} className="animate-spin" />}
                    {editing ? 'Сохранить и отправить' : 'Отправить на согласование'}
                </button>
            </>
        );
    }

    return (
        <IosModal
            open={open}
            onClose={requestClose}
            onBack={!editing && kind ? () => { setKindMotion('motion-safe:animate-pop-in'); setReveal(false); set('request_kind', null); } : null}
            title={editing ? `Заявка №${request.id} — доработка` : (kind ? (regular ? 'Оплата по действующему/регулярному обязательству' : 'Новый закуп') : 'Создать заявку')}
            subtitle={editing ? request.expense_name : (kind ? '' : 'Выберите тип обращения')}
            maxWidth="max-w-3xl"
            footer={footer}
            // Выбор типа — две плитки, форма — во весь рост окна: рамка доезжает, а не перескакивает.
            sizeKey={kind ? 'form' : 'kind'}
        >
            <div key={kind ? 'form' : 'kind'} ref={formRef} className={`space-y-4 ${kindMotion}`}>
                <div ref={errorRef} className="scroll-mt-4"><ErrorBox text={error} /></div>

                {editing && request.clarify_reason && (
                    <NoticeBox text="">
                        <span className="font-medium">
                            {request.clarify_reason === 'rework' ? 'Вернули на доработку' : 'Требуется уточнение'}
                            {request.clarify_by_name ? ` — ${request.clarify_by_name}` : ''}.
                        </span>
                        {request.clarify_comment ? ` ${request.clarify_comment}` : ''}
                    </NoticeBox>
                )}

                {!kind && (
                    <div className="grid gap-3 sm:grid-cols-2">
                        {KIND_CARDS.map((card) => (
                            <button
                                key={card.value}
                                type="button"
                                onClick={() => { setKindMotion('motion-safe:animate-push-in'); set('request_kind', card.value); }}
                                className="flex flex-col gap-2 rounded-2xl bg-white p-4 text-left ring-1 ring-slate-200/70 transition hover:ring-blue-300 hover:shadow-[0_2px_12px_rgba(15,23,42,0.06)] active:scale-[0.99]"
                            >
                                <span className="grid h-9 w-9 place-items-center rounded-xl bg-slate-100 text-slate-600">
                                    <card.icon size={18} />
                                </span>
                                <span className="text-[15px] font-semibold text-slate-900">{card.title}</span>
                                <span className="text-[13px] leading-relaxed text-slate-500">{card.text}</span>
                            </button>
                        ))}
                    </div>
                )}

                {/* ══ Регулярный платёж (п. 4.4) ══ */}
                {regular && (
                    <>
                        <IosSection title="Регулярный платёж">
                            <Field label="Платёж из справочника" required hint={HINTS.template} anchor="fixed_template_id" issue={issueOf('fixed_template_id')}>
                                <FormSelect
                                    value={draft.fixed_template_id}
                                    onChange={pickTemplate}
                                    options={templates.map((item) => ({ value: item.id, label: item.counterparty_name ? `${item.name} · ${item.counterparty_name}` : item.name }))}
                                    placeholder={templates.length ? 'Выберите платёж' : 'Справочник регулярных платежей пуст'}
                                    searchable={templates.length > 8}
                                    disabled={!templates.length}
                                    ariaLabel="Регулярный платёж"
                                />
                            </Field>
                            {template && (
                                <div className="rounded-xl bg-slate-50 px-3 py-1.5">
                                    <Row label="Поставщик">{template.counterparty_name || <span className="text-rose-600">не указан в справочнике</span>}</Row>
                                    <Row label="Договор">{template.contract_number ? `№${template.contract_number}` : null}</Row>
                                    <Row label="Компания">{template.legal_entity_name || <span className="text-rose-600">не указана в справочнике</span>}</Row>
                                    <Row label="Реквизиты">{template.account_text}</Row>
                                    <Row label="Назначение платежа">{template.payment_purpose}</Row>
                                    <Row label="Лимит">{template.amount_limit ? fmtMoney(template.amount_limit) : null}</Row>
                                    <Row label="Периодичность">{PERIOD_META[template.periodicity]?.label}</Row>
                                    <Row label="Согласующий">{template.approver_name}</Row>
                                </div>
                            )}
                        </IosSection>

                        <IosSection title="Оплата">
                            <div className="grid gap-3 sm:grid-cols-2">
                                <Field label="Период оплаты" required hint={HINTS.paymentPeriod} anchor="payment_period" issue={issueOf('payment_period')}>
                                    <FormInput value={draft.payment_period} onChange={(event) => set('payment_period', event.target.value)} maxLength={120} placeholder="Например: октябрь 2026" />
                                </Field>
                                <Field label="Сумма, ₸" required anchor="amount" issue={issueOf('amount')}>
                                    <AmountInput value={draft.amount} onChange={(value) => set('amount', value)} ariaLabel="Сумма" />
                                </Field>
                            </div>
                            {template?.amount_limit && parseAmount(draft.amount) > parseAmount(template.amount_limit) && (
                                <div className="px-1 text-[12.5px] text-amber-700">
                                    Сумма выше лимита платежа ({fmtMoney(template.amount_limit)}) — согласующего выберут общие лимиты и маршруты.
                                </div>
                            )}
                            <div className="grid gap-3 sm:grid-cols-2">
                                <Field label="Номер счёта" required hint={HINTS.invoice} anchor="invoice_number" issue={issueOf('invoice_number')}>
                                    <FormInput value={draft.invoice_number} onChange={(event) => set('invoice_number', event.target.value)} maxLength={100} placeholder="Как в счёте" />
                                </Field>
                                <Field label="Дата счёта" required anchor="invoice_date" issue={issueOf('invoice_date')}>
                                    <FormDate value={draft.invoice_date} onChange={(value) => set('invoice_date', value || '')} ariaLabel="Дата счёта" />
                                </Field>
                            </div>
                            <Field as="div" label="Счёт" required optionalMark={false} anchor="invoice_file" issue={issueOf('invoice_file')}>
                                <InvoiceFile file={invoiceFile} saved={savedInvoices} onPick={setInvoiceFile} onClear={() => setInvoiceFile(null)} />
                            </Field>
                            <div className="grid gap-3 sm:grid-cols-2">
                                <Field label="Подразделение" required hint={HINTS.department} anchor="department_id" issue={issueOf('department_id')}>
                                    <FormSelect value={draft.department_id} onChange={(value) => set('department_id', value)} options={departmentOptions} placeholder="Подразделение" ariaLabel="Подразделение" />
                                </Field>
                                <Field label="Срок оплаты" hint={HINTS.dueOn}>
                                    <FormDate value={draft.due_on} onChange={(value) => set('due_on', value || '')} ariaLabel="Срок оплаты" />
                                </Field>
                            </div>
                            <Field label="Комментарий">
                                <FormText className="min-h-[56px]" value={draft.notes} onChange={(event) => set('notes', event.target.value)} maxLength={4000} />
                            </Field>
                        </IosSection>

                        {extrasSection(REGULAR_EXTRA_FIELDS)}
                    </>
                )}

                {/* ══ Новый закуп (п. 4.1) ══ */}
                {kind === 'purchase' && (
                    <>
                        <IosSection title="Что закупаем">
                            <Field label="Наименование закупа" required hint="Коротко и конкретно: «Бумага А4 для офиса», а не «хоз. товары». Подробности — в позициях ниже." anchor="expense_name" issue={issueOf('expense_name')}>
                                <FormInput value={draft.expense_name} onChange={(event) => set('expense_name', event.target.value)} placeholder="Например: Бумага А4 и канцелярия для офиса" maxLength={300} />
                            </Field>
                            <div className="grid gap-3 sm:grid-cols-2">
                                <Field label="Категория закупа" required hint={HINTS.category} anchor="category_id" issue={issueOf('category_id')}>
                                    <FormSelect
                                        value={draft.category_id}
                                        onChange={(value) => setDraft((prev) => ({ ...prev, category_id: value, subcategory_id: null }))}
                                        options={categories.map((item) => ({ value: item.id, label: item.name }))}
                                        placeholder={categories.length ? 'Выберите категорию' : 'Справочник пуст — заведите категории'}
                                        searchable={categories.length > 8}
                                        ariaLabel="Категория закупа"
                                    />
                                </Field>
                                {/* У категории без подкатегорий поля нет вовсе: заблокированный
                                    список, в котором нечего выбрать, только отвлекает. */}
                                {draft.category_id && subcategories.length > 0 && (
                                    <Field label="Подкатегория" hint={HINTS.subcategory}>
                                        <FormSelect value={draft.subcategory_id} onChange={(value) => set('subcategory_id', value)} options={[{ value: null, label: 'Без подкатегории' }, ...subcategories.map((item) => ({ value: item.id, label: item.name }))]} placeholder="Без подкатегории" ariaLabel="Подкатегория" />
                                    </Field>
                                )}
                            </div>
                            <Field label="Описание и обоснование" required hint={HINTS.justification} anchor="justification" issue={issueOf('justification')}>
                                <FormText className="min-h-[72px]" value={draft.justification} onChange={(event) => set('justification', event.target.value)} maxLength={4000} placeholder="Что покупаем и почему это нужно сейчас" />
                            </Field>
                            <Field as="div" label="Позиции и количество" required optionalMark={false} hint={HINTS.items} anchor="items" issue={issueOf('items')} issueFirst>
                                <PaymentItemsEditor items={draft.items} onChange={(items) => set('items', items)} issue={reveal ? byKey.items?.[0] || null : null} />
                            </Field>
                            <div className="flex flex-wrap gap-x-6 gap-y-3">
                                <Field as="div" label="Тип объекта" required optionalMark={false} hint={HINTS.objectType} anchor="object_type" issue={issueOf('object_type')}>
                                    <Choice value={draft.object_type} options={OBJECT_TYPE_OPTIONS} onChange={(value) => setDraft((prev) => ({ ...prev, object_type: value, accounting_category: value === 'goods' ? prev.accounting_category : null }))} ariaLabel="Тип объекта" />
                                </Field>
                                {draft.object_type === 'goods' && (
                                    <Field as="div" label="Категория учёта" required optionalMark={false} hint={HINTS.accountingCategory} anchor="accounting_category" issue={issueOf('accounting_category')}>
                                        <Choice value={draft.accounting_category} options={ACCOUNTING_CATEGORY_OPTIONS} onChange={(value) => set('accounting_category', value)} ariaLabel="Категория учёта приобретения" />
                                    </Field>
                                )}
                            </div>
                            {draft.object_type === 'goods' && draft.accounting_category === 'asset' && staffed?.asset_keeper === false && !reveal && (
                                <NoticeBox text={`Сейчас так отправить нельзя: ${UNSTAFFED.asset_keeper}.`} />
                            )}
                            <div className="grid gap-3 sm:grid-cols-2">
                                <Field label="Желаемый срок" required hint={HINTS.dueOn} anchor="due_on" issue={issueOf('due_on')}>
                                    <FormDate value={draft.due_on} onChange={(value) => set('due_on', value || '')} placeholder="Выберите дату" ariaLabel="Желаемый срок" />
                                </Field>
                            </div>
                        </IosSection>

                        <IosSection title="Для кого">
                            <div className="grid gap-3 sm:grid-cols-2">
                                <Field label="Компания" required={!entityOptional} hint={entityOptional ? HINTS.legalEntity : HINTS.legalEntityRequired} anchor="legal_entity_id" issue={issueOf('legal_entity_id')}>
                                    <FormSelect
                                        value={draft.legal_entity_id}
                                        onChange={(value) => set('legal_entity_id', value || null)}
                                        options={entityChoiceOptions}
                                        placeholder={!legalEntityOptions.length ? 'Справочник компаний пуст' : (entityOptional ? ENTITY_UNKNOWN : 'Выберите компанию')}
                                        disabled={!legalEntityOptions.length}
                                        ariaLabel="Компания"
                                    />
                                </Field>
                                <Field label="Подразделение" required hint={HINTS.department} anchor="department_id" issue={issueOf('department_id')}>
                                    <FormSelect value={draft.department_id} onChange={(value) => set('department_id', value)} options={departmentOptions} placeholder="Подразделение" ariaLabel="Подразделение" />
                                </Field>
                            </div>
                            {/* Инициатор и руководитель — поля заявки (п. 4.1), но не поля ввода:
                                инициатор — тот, кто заводит заявку, руководителя определяет раздел. */}
                            <div className="rounded-xl bg-slate-50 px-3 py-1.5">
                                <Row label="Инициатор">{initiatorName}</Row>
                                <Row label="Руководитель">
                                    {managerName || <span className="text-slate-500">не определён — этап руководителя будет пропущен</span>}
                                    {managerName && <span className="text-slate-500"> · определён автоматически</span>}
                                </Row>
                            </div>
                            <RequisitesBox entity={legalEntity} />
                        </IosSection>

                        <IosSection title="Поставщики">
                            <Field as="div" label="Альтернативные предложения" required optionalMark={false} hint={HINTS.alternatives}>
                                <Choice value={Boolean(draft.no_alternatives)} options={ALTERNATIVES_OPTIONS} onChange={(value) => set('no_alternatives', value)} ariaLabel="Альтернативные предложения" />
                            </Field>
                            {draft.no_alternatives && (
                                <div className="grid gap-3 sm:grid-cols-2">
                                    <Field label="Причина отсутствия альтернатив" required anchor="no_alternatives_reason" issue={issueOf('no_alternatives_reason')}>
                                        <FormSelect value={draft.no_alternatives_reason} onChange={(value) => set('no_alternatives_reason', value)} options={reasonOptions} placeholder="Выберите причину" ariaLabel="Причина отсутствия альтернатив" />
                                    </Field>
                                    <Field label="Пояснение" required={draft.no_alternatives_reason === 'other'} anchor="no_alternatives_comment" issue={issueOf('no_alternatives_comment')}>
                                        <FormInput value={draft.no_alternatives_comment} onChange={(event) => set('no_alternatives_comment', event.target.value)} maxLength={2000} />
                                    </Field>
                                </div>
                            )}
                            <Field as="div" label={draft.no_alternatives ? 'Поставщик' : 'Варианты поставщиков'} required optionalMark={false} hint={draft.no_alternatives ? null : { ...HINTS.offers, intro: `${HINTS.offers.intro} Нужно не меньше ${settings?.min_suppliers ?? 3}.` }} anchor="offers" issue={issueOf('offers')} issueFirst>
                                <PaymentOffersEditor
                                    offers={draft.offers}
                                    onChange={(next) => set('offers', next)}
                                    counterpartyOptions={counterpartyOptions}
                                    savedFilesByOffer={savedFilesByOffer}
                                    single={Boolean(draft.no_alternatives)}
                                    issues={reveal ? byKey.offers || [] : []}
                                />
                            </Field>
                            {!draft.no_alternatives && (
                                <Field label="Обоснование выбора поставщика" required hint={HINTS.choiceReason} anchor="supplier_choice_reason" issue={issueOf('supplier_choice_reason')}>
                                    <FormText className="min-h-[56px]" value={draft.supplier_choice_reason} onChange={(event) => set('supplier_choice_reason', event.target.value)} maxLength={2000} />
                                </Field>
                            )}
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
                        </IosSection>

                        <IosSection title="Способ оплаты">
                            <Field as="div" label="Как платим" required optionalMark={false} hint={HINTS.paymentMethod} anchor="payment_method" issue={issueOf('payment_method')}>
                                <Choice value={draft.payment_method} options={PAYMENT_METHOD_OPTIONS} onChange={(value) => set('payment_method', value)} ariaLabel="Способ оплаты" />
                            </Field>
                            {draft.payment_method === 'card' && staffed?.finance === false && !reveal && (
                                <NoticeBox text={`Сейчас так отправить нельзя: ${UNSTAFFED.finance}.`} />
                            )}

                            {draft.payment_method === 'invoice' && (
                                <>
                                    {supplierId && (contractOptions.length > 0 || accountOptions.length > 0) && (
                                        <div className="grid gap-3 sm:grid-cols-2">
                                            {contractOptions.length > 0 && (
                                                <Field label="Договор" required={total > threshold} hint={HINTS.contract}>
                                                    <FormSelect value={contractId} onChange={(value) => set('contract_id', value)} options={[{ value: null, label: 'Без договора' }, ...contractOptions]} placeholder="Без договора" ariaLabel="Договор" />
                                                </Field>
                                            )}
                                            {accountOptions.length > 0 && (
                                                <Field label="Реквизиты поставщика" hint="Счёт поставщика из справочника «Банковские реквизиты поставщиков» — бухгалтерия сверит его со счётом.">
                                                    <FormSelect value={accountId} onChange={(value) => set('counterparty_account_id', value)} options={[{ value: null, label: 'Не указывать' }, ...accountOptions]} placeholder="Не указывать" ariaLabel="Реквизиты поставщика" />
                                                </Field>
                                            )}
                                        </div>
                                    )}
                                    {contractNeeded && (
                                        <NoticeBox text={`Сумма выше ${fmtMoney(threshold)}: счёт не уйдёт в оплату без действующего договора с поставщиком. Договор можно указать сейчас или после согласования.`} />
                                    )}
                                    {invoiceOpen ? (
                                        <>
                                            <div className="grid gap-3 sm:grid-cols-2">
                                                <Field label="Номер счёта" required hint={HINTS.invoice} anchor="invoice_number" issue={issueOf('invoice_number')}>
                                                    <FormInput value={draft.invoice_number} onChange={(event) => set('invoice_number', event.target.value)} maxLength={100} placeholder="Как в счёте" />
                                                </Field>
                                                <Field label="Дата счёта" required anchor="invoice_date" issue={issueOf('invoice_date')}>
                                                    <FormDate value={draft.invoice_date} onChange={(value) => set('invoice_date', value || '')} ariaLabel="Дата счёта" />
                                                </Field>
                                            </div>
                                            {/* Без подписи, как и было: файл счёта стоит сразу под полями счёта. */}
                                            <Field as="div" anchor="invoice_file" issue={issueOf('invoice_file')}>
                                                <InvoiceFile file={invoiceFile} saved={savedInvoices} onPick={setInvoiceFile} onClear={() => setInvoiceFile(null)} />
                                            </Field>
                                        </>
                                    ) : (
                                        <div className="flex flex-wrap items-center gap-2 text-[12.5px] text-slate-500">
                                            Счёт можно приложить после согласования — раздел попросит его перед оплатой.
                                            <button type="button" className="font-medium text-blue-600 hover:underline" onClick={() => setInvoiceOpen(true)}>Счёт уже есть</button>
                                        </div>
                                    )}
                                </>
                            )}

                            {draft.payment_method === 'card' && (
                                <>
                                    <Field as="div" label="Получатель" required optionalMark={false} hint={HINTS.cardRecipient} anchor="card_recipient" issue={issueOf('card_recipient')}>
                                        <Choice value={draft.card_recipient} options={CARD_RECIPIENT_OPTIONS} onChange={(value) => setDraft((prev) => ({ ...prev, card_recipient: value, card_id: null }))} ariaLabel="Чью карту пополнить" />
                                    </Field>
                                    {draft.card_recipient === 'employee' && (
                                        <Field label="Сотрудник" required anchor="card_holder_user_id" issue={issueOf('card_holder_user_id')}>
                                            <UserSelect users={users} value={draft.card_holder_user_id} onChange={(value) => setDraft((prev) => ({ ...prev, card_holder_user_id: value, card_id: null }))} placeholder="Чья карта" />
                                        </Field>
                                    )}
                                    {draft.card_recipient === 'supplier' && (
                                        <Field label="ФИО владельца карты" required anchor="card_holder_name" issue={issueOf('card_holder_name')}>
                                            <FormInput value={draft.card_holder_name} onChange={(event) => set('card_holder_name', event.target.value)} maxLength={200} placeholder="Как на карте" />
                                        </Field>
                                    )}
                                    {draft.card_recipient && (cardKept ? (
                                        <Field as="div" label="Номер карты" required optionalMark={false} hint={HINTS.cardNumber}>
                                            <div className="flex flex-wrap items-center gap-2 text-[14px] tabular-nums text-slate-900">
                                                {request.card_mask}
                                                <button type="button" className="text-[12.5px] font-medium text-blue-600 hover:underline" onClick={() => setCardEditing(true)}>Изменить</button>
                                            </div>
                                        </Field>
                                    ) : (
                                        <div className="grid gap-3 sm:grid-cols-2">
                                            {cardSelect && (
                                                <Field label="Карта" required hint="Сохранённые карты получателя из справочника. Показаны последние четыре цифры.">
                                                    <FormSelect
                                                        value={draft.card_id ?? NEW_CARD}
                                                        onChange={(value) => set('card_id', value === NEW_CARD ? null : value)}
                                                        options={[...cardOptions.map((item) => ({ value: item.id, label: `${item.mask} · ${item.holder_name}` })), { value: NEW_CARD, label: 'Другая карта — ввести номер' }]}
                                                        ariaLabel="Карта получателя"
                                                    />
                                                </Field>
                                            )}
                                            {!draft.card_id && (
                                                <Field label="Номер карты" required hint={HINTS.cardNumber} anchor="card_number" issue={issueOf('card_number')}>
                                                    <FormInput
                                                        className={`tabular-nums ${lunaWarn ? 'ring-2 ring-amber-300' : ''}`}
                                                        inputMode="numeric"
                                                        autoComplete="off"
                                                        value={cardNumberLabel(draft.card_number)}
                                                        onChange={(event) => set('card_number', cardDigits(event.target.value).slice(0, 19))}
                                                        placeholder="0000 0000 0000 0000"
                                                        aria-label="Номер карты"
                                                    />
                                                </Field>
                                            )}
                                        </div>
                                    ))}
                                    {lunaWarn && (
                                        <div className="px-1 text-[12.5px] text-amber-700">Проверьте номер: контрольная цифра не сходится — возможно, опечатка.</div>
                                    )}
                                    <Field label="Назначение перевода" required hint={HINTS.paymentPurpose} anchor="payment_purpose" issue={issueOf('payment_purpose')}>
                                        <FormInput value={draft.payment_purpose} onChange={(event) => set('payment_purpose', event.target.value)} maxLength={2000} placeholder="На что пойдут деньги" />
                                    </Field>
                                </>
                            )}
                        </IosSection>

                        {extrasSection(EXTRA_FIELDS)}
                    </>
                )}

                {/* Согласованная заявка после ответа возвращается тому, кто спросил, — маршрут
                    согласования ей показывать незачем. Но человек должен знать, какая правка
                    отправит её на согласование заново. */}
                {approvedBefore && (
                    <div className="rounded-2xl bg-slate-50 px-4 py-3 text-[13px] text-slate-600">
                        Заявка уже согласована. Смена суммы, поставщика, компании, подразделения, категории, проекта,
                        способа оплаты или карты отправит её на согласование заново.
                    </div>
                )}

                {/* Кому уйдёт заявка — маршрут определяет система, инициатор его только видит (п. 6). */}
                {kind && route && !approvedBefore && (
                    <div className="rounded-2xl bg-slate-50 px-4 py-3 text-[13px]">
                        <div className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-slate-500">
                            Маршрут согласования <FieldHint hint="Кому направить заявку, определяет раздел: по лимитам и маршрутам согласования из справочников. Выбирать согласующего вручную не нужно." />
                        </div>
                        <div className="mt-1 flex flex-wrap items-center gap-x-1.5 gap-y-1 text-slate-900">
                            {/* Компания не указана — первой заявку получит бухгалтерия, а согласующего
                                матрица (п. 6) выберет уже по компании: называть его заранее нельзя. Заявке
                                первой версии компания нужна при отправке — этапа у неё не будет. */}
                            {entityStep && (
                                <>
                                    <span>Бухгалтерия <span className="text-slate-500">· уточнит компанию</span></span>
                                    <ArrowRight size={13} className="text-slate-400" />
                                </>
                            )}
                            {route.managerStep && (
                                <>
                                    <span>{routeManager || <span className="text-slate-500">руководитель не определён — этап будет пропущен</span>}</span>
                                    <ArrowRight size={13} className="text-slate-400" />
                                </>
                            )}
                            {entityStep ? <span className="text-slate-500">согласующего выберут по компании</span> : (
                                <>
                                    <span className="font-medium">{route.approver}</span>
                                    <ApprovalHint basis={preview.route} />
                                </>
                            )}
                        </div>
                        {route.basisText && !entityStep && <div className="mt-0.5 text-[12.5px] text-slate-500">{route.basisText}</div>}
                    </div>
                )}
            </div>
        </IosModal>
    );
};

export default PaymentRequestForm;
