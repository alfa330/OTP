import React, { useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import { Loader2, Pencil } from 'lucide-react';
import { iosBtnGhost, iosBtnPrimary, iosBtnSecondary, iosInput } from '../ui/ios';
import IosDatePicker from '../ui/DatePicker';
import CustomSelect from '../ui/CustomSelect';
import {
    HINTS, ORIGINALS_OPTIONS, PAYMENT_ORDER_OPTIONS, POWER_OF_ATTORNEY_OPTIONS, PREVIOUSLY_PAID_OPTIONS,
    RECEIVED_OPTIONS, SUPPLIER_KIND_OPTIONS, VAT_OPTIONS, approverBasisLine, attachmentKindsForStep, fmtDate,
    fmtMoney, invoiceDescriptionDraft, lastPaymentToCounterparty, parseAmount, previousPaymentNote, receiptComment,
    requisitesDraft, routeSummary, stepReturnNote, supplierLabel, todayISO,
} from './paymentsMeta';
import {
    AmountInput, AttachmentList, Choice, CopyButton, Field, FileChips, FilePicker, NoticeBox, RouteHint,
} from './paymentsUi';

/*
 * Форма отписки на текущем шаге — у каждого из двенадцати шагов своя.
 *
 * Три правила, по которым она собрана (решение владельца 06.10.2026: «каждый
 * шаг сделай более удобным», «варианты пусть будут селектором»).
 *
 * 1. ВАРИАНТЫ ВЫБИРАЮТ, А НЕ ПИШУТ. «ТОО или ИП», «с НДС или без», «платили или
 *    нет», «товар или услуга», «оригинал передан или позже» — сегменты из двух-трёх
 *    вариантов с пояснением под «i». Отписку из выбранного собирает раздел
 *    (paymentsMeta: previousPaymentNote, receiptComment), комментарий остаётся
 *    для того, что в варианты не уложилось.
 *
 * 2. НУЖНОЕ ДЛЯ РЕШЕНИЯ — ПОД РУКОЙ. Тому, кто подтверждает закуп, — реестр
 *    поставщиков и КП; тому, кто проверяет и подтверждает счёт, — сам счёт и
 *    отписка бухгалтерии; инициатору на шаге 6 — реквизиты с кнопкой
 *    «Скопировать». Это короткие кнопки-файлы и одна-две строки, а не копия
 *    разделов карточки.
 *
 * 3. ЧТО ОСТАЛОСЬ СДЕЛАТЬ — СЛОВАМИ У КНОПКИ. Кнопка шага неактивна, пока не
 *    заполнено обязательное, и рядом написано, чего именно не хватает: человек,
 *    который пришёл впервые, не должен угадывать, почему кнопка серая. Сервер
 *    проверяет то же самое (workflow.missing_requirements) — клиентская проверка
 *    только подсказка.
 *
 * Состояние формы живёт здесь и сбрасывается сменой шага: карточка монтирует
 * компонент с key из номера заявки и шага.
 */

const dateTrigger = 'flex w-full items-center gap-2 rounded-xl bg-slate-100 px-3.5 py-2.5 '
    + 'text-[14px] tabular-nums text-slate-900 border-0 transition hover:bg-slate-200/70 '
    + 'focus:outline-none focus:ring-2 focus:ring-blue-500/70 [&>span]:flex-1 [&>span]:text-left';

const PREVIEW_DEBOUNCE_MS = 300;

const initialFields = (stepNo, request) => {
    if (stepNo === 4) {
        return { supplier_kind: request.supplier_kind ?? null, supplier_vat: request.supplier_vat ?? null };
    }
    if (stepNo === 5) {
        return { legal_entity_id: request.legal_entity_id ?? null, invoice_requisites: request.invoice_requisites || '' };
    }
    if (stepNo === 7) {
        return {
            invoice_description: request.invoice_description || '',
            invoice_number: request.invoice_number || '',
            invoice_date: request.invoice_date ? String(request.invoice_date).slice(0, 10) : '',
            legal_entity_id: request.legal_entity_id ?? null,
            contract_id: request.contract_id ?? null,
            needs_power_of_attorney: Boolean(request.needs_power_of_attorney),
            needs_payment_order: Boolean(request.needs_payment_order),
        };
    }
    if (stepNo === 10) {
        return {
            paid_on: request.paid_on ? String(request.paid_on).slice(0, 10) : todayISO(),
            paid_amount: request.paid_amount ?? request.amount ?? 0,
        };
    }
    return {};
};

const PaymentStepForm = ({
    request, step, steps, data, permissions, dictionaries, apiBaseUrl, headers, busy, showToast,
    canRemoveAttachment, onRemoveAttachment, onComplete, onReturn, onReject, onEdit,
}) => {
    const no = step.step_no;
    const [fields, setFields] = useState(() => initialFields(no, request));
    const [comment, setComment] = useState('');
    const [files, setFiles] = useState([]);
    const [mode, setMode] = useState(null);          // 'return' | 'reject'
    const [reason, setReason] = useState('');
    const [preview, setPreview] = useState(null);
    /* Шаг 8: «платили / не платили» и последняя оплата. Если в реестре раздела
       есть оплата этому поставщику — выбор и поля подставлены из неё. Если нет —
       выбор НЕ подставляется: реестр копится с запуска раздела, и «у нас записей
       нет» ещё не значит «не платили» — это бухгалтер решает сам. Один раз, при
       открытии шага: обновление карточки не должно затирать выбранное. */
    const [previous, setPrevious] = useState(() => {
        const last = no === 8 ? lastPaymentToCounterparty(data?.history) : null;
        return last
            ? { paid: true, paidOn: String(last.paid_on).slice(0, 10), paidAmount: last.paid_amount ?? last.amount ?? 0 }
            : { paid: null, paidOn: '', paidAmount: 0 };
    });
    // Шаг 11: «товар / услуга» и судьба оригинала.
    const [receipt, setReceipt] = useState({ received: null, originals: null });
    const set = (key, value) => setFields((prev) => ({ ...prev, [key]: value }));

    const attachments = data?.attachments || [];
    const byStep = useMemo(() => {
        const map = {};
        attachments.forEach((item) => { (map[item.step_no || 0] ||= []).push(item); });
        return map;
    }, [attachments]);
    const ownFiles = byStep[no] || [];
    const threshold = data?.contract_check?.threshold || 300000;
    const amount = parseAmount(request.amount);
    const returned = stepReturnNote(data?.events, no);
    const fileKinds = useMemo(() => attachmentKindsForStep(step), [step]);
    const takesFiles = Boolean(step.files?.length > 0 || no === 1);

    const legalEntities = dictionaries?.legal_entities || [];
    const legalEntityOptions = useMemo(() => legalEntities.map((item) => ({ value: item.id, label: item.name })), [legalEntities]);
    const legalEntity = legalEntities.find((item) => item.id === fields.legal_entity_id) || null;
    const contractOptions = useMemo(() => (dictionaries?.contracts || [])
        .filter((item) => item.counterparty_id === request.counterparty_id)
        .map((item) => ({
            value: item.id,
            label: `№${item.number}${item.ends_on ? ` до ${fmtDate(item.ends_on)}` : ''}${item.status !== 'active' ? ' · недействующий' : ''}`,
        })), [dictionaries, request.counterparty_id]);

    /* Шаг 4: если бухгалтерия уже заполнила карточку контрагента в справочнике,
       форма и НДС подставляются оттуда — инициатору остаётся подтвердить.
       Признак заполненной карточки — указанная форма: у контрагента, заведённого
       одним названием из заявки, НДС «нет» означает лишь «не указано». */
    const prefilled = useRef(false);
    useEffect(() => {
        if (no !== 4 || prefilled.current) return;
        const counterparty = (dictionaries?.counterparties || []).find((item) => item.id === request.counterparty_id);
        if (!counterparty) return;
        prefilled.current = true;
        if (!counterparty.kind) return;
        setFields((prev) => (prev.supplier_kind || prev.supplier_vat !== null
            ? prev
            : { ...prev, supplier_kind: counterparty.kind, supplier_vat: Boolean(counterparty.vat_payer) }));
    }, [no, dictionaries, request.counterparty_id]);

    /* Шаг 5: выбрали юр. лицо — реквизиты подставились из справочника. Чужой
       текст не затираем: подстановка идёт только в пустое поле или поверх своей
       же прошлой подстановки. */
    const autoRequisites = useRef('');
    const pickLegalEntity = (id) => {
        const entity = legalEntities.find((item) => item.id === id) || null;
        const draft = requisitesDraft(entity);
        const typed = String(fields.invoice_requisites || '').trim();
        const replace = !typed || typed === autoRequisites.current.trim();
        if (replace) autoRequisites.current = draft;
        setFields((prev) => ({ ...prev, legal_entity_id: id, invoice_requisites: replace ? draft : prev.invoice_requisites }));
    };
    /* Юр. лицо уже указано в заявке (инициатор выбрал его при создании) —
       реквизиты подставляются сразу, без повторного выбора того же юр. лица. */
    const requisitesPrefilled = useRef(false);
    useEffect(() => {
        if (no !== 5 || requisitesPrefilled.current || !legalEntities.length) return;
        requisitesPrefilled.current = true;
        const draft = requisitesDraft(legalEntities.find((item) => item.id === fields.legal_entity_id) || null);
        if (!draft) return;
        setFields((prev) => {
            if (String(prev.invoice_requisites || '').trim()) return prev;
            autoRequisites.current = draft;
            return { ...prev, invoice_requisites: draft };
        });
        // Один раз, когда пришёл справочник: дальше поле ведёт человек.
    }, [no, legalEntities]);

    /* Счёт на шаге 7 может остановиться на правиле договора: файл сервер при этом
       уже сохранил, а шаг остался текущим. Убираем из выбранных то, что уже лежит
       в заявке, — иначе повторная отправка приложила бы тот же файл второй раз. */
    useEffect(() => {
        if (!ownFiles.length) return;
        const stored = new Set(ownFiles.map((item) => `${item.file_name}|${item.file_size}`));
        setFiles((prev) => {
            const rest = prev.filter((entry) => !stored.has(`${entry.file.name}|${entry.file.size}`));
            return rest.length === prev.length ? prev : rest;
        });
    }, [ownFiles]);

    /* Шаг 7: описание счёта — заготовка из данных заявки; обновляется вслед за
       выбором юр. лица, пока человек не начал править её сам. */
    const autoDescription = useRef('');
    const legalEntityName = legalEntity?.name || (fields.legal_entity_id ? request.legal_entity_name : '') || '';
    useEffect(() => {
        if (no !== 7) return;
        const typed = String(fields.invoice_description || '').trim();
        if (typed && typed !== autoDescription.current.trim()) return;
        const draft = invoiceDescriptionDraft(request, legalEntityName);
        autoDescription.current = draft;
        if (draft !== fields.invoice_description) setFields((prev) => ({ ...prev, invoice_description: draft }));
        // Поля формы в зависимостях не нужны: заготовка пересобирается только
        // вслед за заявкой и выбранным юр. лицом, а не за каждой буквой.
    }, [no, request, legalEntityName]);

    /* Шаг 7: ещё ДО отправки видно, кто согласует счёт (Учредитель или
       согласующий по Приказу) и не остановит ли его правило договора. Сервер
       считает тем же кодом, что и при отписке, — расхождения быть не может. */
    const previewContract = fields.contract_id ?? null;
    const previewDate = fields.invoice_date || '';
    useEffect(() => {
        if (no !== 7) return undefined;
        const timer = setTimeout(() => {
            const params = new URLSearchParams();
            params.set('amount', String(request.amount || 0));
            if (request.project_id) params.set('project_id', request.project_id);
            if (request.counterparty_id) params.set('counterparty_id', request.counterparty_id);
            if (previewContract) params.set('contract_id', previewContract);
            if (previewDate) params.set('on_date', previewDate);
            axios.get(`${apiBaseUrl}/api/payments/route-preview?${params}`, { headers: headers() })
                .then((response) => setPreview(response.data || null))
                .catch(() => setPreview(null));
        }, PREVIEW_DEBOUNCE_MS);
        return () => clearTimeout(timer);
    }, [no, apiBaseUrl, headers, request.amount, request.project_id, request.counterparty_id, previewContract, previewDate]);

    const lastPayment = useMemo(() => (no === 8 ? lastPaymentToCounterparty(data?.history) : null), [no, data]);

    const previewReason = no === 7 && preview?.contract_check && !preview.contract_check.ok ? preview.contract_check.reason : '';
    const previewApprover = no === 7 && preview?.route ? routeSummary(preview.route) : null;
    const contractRequired = amount > threshold;

    /* Чего не хватает, чтобы отписаться, — словами. Повторяет серверную
       проверку шага, чтобы человек узнал об этом до нажатия, а не после. */
    const missing = [];
    if (step.files_required && !ownFiles.length && !files.length) {
        missing.push(no === 1 ? 'приложите реестр поставщиков или КП' : (no === 7 ? 'приложите счёт' : 'приложите АВР или накладную'));
    }
    if (no === 4) {
        if (!fields.supplier_kind) missing.push('выберите, кто поставщик');
        if (fields.supplier_vat === null || fields.supplier_vat === undefined) missing.push('выберите, с НДС он или без');
    }
    if (no === 5 && !String(fields.invoice_requisites || '').trim()) missing.push('укажите реквизиты для счёта');
    if (no === 7) {
        if (ownFiles.length + files.length > 0 && ![...ownFiles, ...files].some((item) => item.kind === 'invoice')) {
            missing.push('у приложенного файла выберите вид «Счёт на оплату»');
        }
        if (!String(fields.invoice_description || '').trim()) missing.push('опишите счёт');
        if (!fields.legal_entity_id) missing.push('выберите юр. лицо плательщика');
        // У заявки из календаря платежей контрагента может не быть — поля для него
        // на шаге нет, он указывается в самой заявке.
        if (!request.counterparty_id) missing.push('укажите контрагента в заявке — кнопка «Изменить» внизу карточки');
    }
    if (no === 8) {
        if (previous.paid === null) missing.push('выберите, платили ли этому поставщику раньше');
        if (previous.paid === true && !(previous.paidOn && parseAmount(previous.paidAmount) > 0)) {
            missing.push('укажите дату и сумму последней оплаты');
        }
    }
    if (no === 10) {
        if (!(parseAmount(fields.paid_amount) > 0)) missing.push('укажите оплаченную сумму');
        const kinds = new Set([...ownFiles, ...files].map((item) => item.kind));
        if (request.needs_payment_order && !kinds.has('payment_order')) missing.push('приложите платёжное поручение');
        if (request.needs_power_of_attorney && !kinds.has('power_of_attorney')) missing.push('приложите доверенность');
    }
    if (no === 11) {
        if (ownFiles.length + files.length > 0 && ![...ownFiles, ...files].some((item) => item.kind === 'act')) {
            missing.push('у приложенного файла выберите вид «АВР / накладная»');
        }
        if (!receipt.received) missing.push('выберите, что получили');
        if (!receipt.originals) missing.push('выберите, где оригинал документа');
    }

    const complete = () => {
        const payload = { ...fields };
        let note = comment.trim();
        if (no === 8) payload.previous_payment_note = previousPaymentNote(previous);
        if (no === 11) note = [receiptComment(receipt), note].filter(Boolean).join('\n');
        onComplete({ fields: payload, comment: note, files });
    };

    const invoiceFiles = (byStep[7] || []).filter((item) => item.kind === 'invoice');
    const closingStep = (steps || []).find((item) => item.step_no === 11);

    return (
        <div data-step-form={no} className="mt-2.5 space-y-3 rounded-2xl bg-white p-3.5 ring-1 ring-blue-100">
            {permissions.acting_as_admin && (
                <div className="text-[12px] text-slate-500">
                    Вы отписываетесь за ответственного ({step.assignee_name || step.role_label}) как администратор раздела — это будет видно в истории.
                </div>
            )}

            {returned && (
                <NoticeBox text="">
                    <span className="font-medium">Вернули на доработку{returned.actor_name ? ` — ${returned.actor_name}` : ''}.</span>
                    {returned.comment ? ` ${returned.comment}` : ''}
                </NoticeBox>
            )}
            {/* Блок «ожидает договор» на 7-м шаге показывает предпросмотр —
                он свежее записанной причины и учитывает выбранный сейчас договор. */}
            {request.block_code && no !== 7 && <NoticeBox text={request.block_reason} />}
            {previewReason && <NoticeBox text={previewReason} />}

            {/* ── Под рукой: то, что нужно для решения на этом шаге ── */}
            {(no === 2 || no === 3) && (
                <FileChips label="Документы к закупу:" attachments={byStep[1]} apiBaseUrl={apiBaseUrl} headers={headers} showToast={showToast} />
            )}
            {no === 5 && (
                <div className="text-[12.5px] text-slate-600">
                    Поставщик: <span className="text-slate-900">{request.counterparty_name || '—'}</span>
                    {supplierLabel(request) ? ` · ${supplierLabel(request)}` : ''}
                    {' · '}счёт на {fmtMoney(amount)}
                </div>
            )}
            {no === 6 && request.invoice_requisites && (
                <div className="rounded-xl bg-slate-50 px-3 py-2">
                    <div className="flex items-center justify-between gap-2">
                        <span className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">Реквизиты от бухгалтерии</span>
                        <CopyButton text={request.invoice_requisites} />
                    </div>
                    <div className="whitespace-pre-line break-words text-[13px] text-slate-800">{request.invoice_requisites}</div>
                </div>
            )}
            {(no === 8 || no === 9 || no === 10) && (
                <FileChips label="Счёт:" attachments={invoiceFiles.length ? invoiceFiles : byStep[7]} apiBaseUrl={apiBaseUrl} headers={headers} showToast={showToast} />
            )}
            {no === 9 && request.previous_payment_note && (
                <div className="text-[12.5px] text-slate-600">
                    Проверка бухгалтерии: <span className="text-slate-900">{request.previous_payment_note}</span>
                </div>
            )}
            {no === 12 && (
                <>
                    <FileChips label="Закрывающие документы:" attachments={byStep[11]} apiBaseUrl={apiBaseUrl} headers={headers} showToast={showToast} />
                    {closingStep?.comment && <div className="text-[12.5px] text-slate-600">Инициатор: <span className="text-slate-900">{closingStep.comment}</span></div>}
                </>
            )}

            {/* ── Поля шага ── */}
            {no === 4 && (
                <>
                    <div className="flex flex-wrap gap-x-6 gap-y-3">
                        <Field as="div" label="Поставщик" required optionalMark={false} hint={HINTS.supplierKind}>
                            <Choice value={fields.supplier_kind} options={SUPPLIER_KIND_OPTIONS} onChange={(value) => set('supplier_kind', value)} ariaLabel="Форма поставщика" />
                        </Field>
                        <Field as="div" label="НДС" required optionalMark={false} hint={HINTS.supplierVat}>
                            <Choice value={fields.supplier_vat} options={VAT_OPTIONS} onChange={(value) => set('supplier_vat', value)} ariaLabel="НДС поставщика" />
                        </Field>
                    </div>
                    <div className={`text-[12.5px] ${contractRequired ? 'text-amber-700' : 'text-slate-500'}`}>
                        {contractRequired
                            ? `Сумма ${fmtMoney(amount)} — свыше ${fmtMoney(threshold)}: на шаге 7 понадобится действующий договор с поставщиком.`
                            : `Сумма ${fmtMoney(amount)} — до ${fmtMoney(threshold)}: договор с поставщиком не обязателен.`}
                    </div>
                </>
            )}

            {no === 5 && (
                <>
                    {legalEntityOptions.length === 0 && (
                        <NoticeBox tone="slate" text="В справочнике пока нет наших юр. лиц. Без юр. лица плательщика счёт не пройдёт шаг 7 — заведите его во вкладке «Справочники» → «Юр. лица» вместе с реквизитами: дальше они будут подставляться сами." />
                    )}
                    {legalEntityOptions.length > 0 && (
                        <Field label="Наше юр. лицо" hint={{ ...HINTS.legalEntity, outro: 'Реквизиты подставятся из справочника «Юр. лица».' }}>
                            <CustomSelect value={fields.legal_entity_id ?? null} onChange={pickLegalEntity} options={legalEntityOptions} placeholder="Выберите юр. лицо" variant="ios" textClassName="text-[14px] text-slate-900" ariaLabel="Наше юр. лицо" />
                        </Field>
                    )}
                    <Field label="Реквизиты для счёта" required optionalMark={false} hint="Название, БИН, банк, ИИК, БИК — то, на что поставщик выставит счёт. Инициатор перешлёт их поставщику.">
                        <textarea className={`${iosInput} min-h-[96px] resize-y`} value={fields.invoice_requisites || ''} onChange={(event) => set('invoice_requisites', event.target.value)} maxLength={4000} />
                    </Field>
                </>
            )}

            {no === 7 && (
                <>
                    <Field label="Описание счёта" required optionalMark={false} hint="Одним текстом: что закупается, за какой период, оплата с какого на какое юр. лицо, сумма, отдел. Заготовка собрана из заявки — проверьте и поправьте.">
                        <textarea className={`${iosInput} min-h-[96px] resize-y`} value={fields.invoice_description || ''} onChange={(event) => set('invoice_description', event.target.value)} maxLength={4000} />
                    </Field>
                    <div className="grid gap-3 sm:grid-cols-2">
                        <Field label="Номер счёта">
                            <input className={iosInput} value={fields.invoice_number || ''} onChange={(event) => set('invoice_number', event.target.value)} maxLength={100} placeholder="Как в счёте" />
                        </Field>
                        <Field label="Дата счёта" hint="Дата из счёта. По ней проверяется, действуют ли договор и Приказ. Не указана — берётся сегодняшняя.">
                            <IosDatePicker value={fields.invoice_date || ''} onChange={(value) => set('invoice_date', value || '')} allowEmpty placeholder="Сегодня" triggerClassName={dateTrigger} ariaLabel="Дата счёта" />
                        </Field>
                    </div>
                    <div className="grid gap-3 sm:grid-cols-2">
                        <Field label="Юр. лицо плательщика" required optionalMark={false} hint={HINTS.legalEntity}>
                            <CustomSelect value={fields.legal_entity_id ?? null} onChange={(value) => set('legal_entity_id', value)} options={legalEntityOptions} placeholder={legalEntityOptions.length ? 'Выберите' : 'Справочник пуст — заведите юр. лица'} variant="ios" textClassName="text-[14px] text-slate-900" ariaLabel="Юр. лицо" />
                        </Field>
                        <Field label="Договор" required={contractRequired} hint={HINTS.contract}>
                            <CustomSelect value={fields.contract_id ?? null} onChange={(value) => set('contract_id', value)} options={[{ value: null, label: 'Без договора' }, ...contractOptions]} placeholder="Без договора" variant="ios" textClassName="text-[14px] text-slate-900" ariaLabel="Договор" />
                        </Field>
                    </div>
                    <div className="flex flex-wrap gap-x-6 gap-y-3">
                        <Field as="div" label="Доверенность" optionalMark={false} hint={HINTS.powerOfAttorney}>
                            <Choice value={Boolean(fields.needs_power_of_attorney)} options={POWER_OF_ATTORNEY_OPTIONS} onChange={(value) => set('needs_power_of_attorney', value)} ariaLabel="Доверенность" />
                        </Field>
                        <Field as="div" label="Платёжное поручение" optionalMark={false} hint={HINTS.paymentOrder}>
                            <Choice value={Boolean(fields.needs_payment_order)} options={PAYMENT_ORDER_OPTIONS} onChange={(value) => set('needs_payment_order', value)} ariaLabel="Платёжное поручение" />
                        </Field>
                    </div>
                    {previewApprover && !previewReason && (
                        <div className="rounded-xl bg-slate-50 px-3 py-2 text-[12.5px]">
                            <div className="flex items-center gap-1.5">
                                <span className="text-slate-500">Счёт согласует</span>
                                <span className="font-medium text-slate-900">{previewApprover.approver}</span>
                                <RouteHint basis={preview.route} />
                            </div>
                            {approverBasisLine(preview.route) && <div className="mt-0.5 text-slate-500">{approverBasisLine(preview.route)}</div>}
                        </div>
                    )}
                </>
            )}

            {no === 8 && (
                <>
                    <Field as="div" label="Этому поставщику уже платили?" required optionalMark={false} hint={HINTS.previouslyPaid}>
                        <Choice value={previous.paid} options={PREVIOUSLY_PAID_OPTIONS} onChange={(value) => setPrevious((prev) => ({ ...prev, paid: value }))} ariaLabel="Платили ли раньше" />
                    </Field>
                    {previous.paid === true && (
                        <div className="grid gap-3 sm:grid-cols-2">
                            <Field label="Дата последней оплаты" required optionalMark={false}>
                                <IosDatePicker value={previous.paidOn || ''} onChange={(value) => setPrevious((prev) => ({ ...prev, paidOn: value || '' }))} allowEmpty placeholder="Выберите дату" triggerClassName={dateTrigger} ariaLabel="Дата последней оплаты" />
                            </Field>
                            <Field label="Сумма последней оплаты" required optionalMark={false}>
                                <AmountInput value={previous.paidAmount} onChange={(value) => setPrevious((prev) => ({ ...prev, paidAmount: value }))} ariaLabel="Сумма последней оплаты" />
                            </Field>
                        </div>
                    )}
                    {lastPayment && (
                        <div className="text-[12.5px] text-slate-500">
                            По реестру: заявка №{lastPayment.request_id} «{lastPayment.expense_name}» — {fmtDate(lastPayment.paid_on)}, {fmtMoney(lastPayment.paid_amount ?? lastPayment.amount)}.
                        </div>
                    )}
                </>
            )}

            {no === 10 && (
                <div className="grid gap-3 sm:grid-cols-2">
                    <Field label="Дата оплаты" required optionalMark={false}>
                        <IosDatePicker value={fields.paid_on || ''} onChange={(value) => set('paid_on', value || '')} allowEmpty placeholder="Сегодня" triggerClassName={dateTrigger} ariaLabel="Дата оплаты" />
                    </Field>
                    <Field label="Оплачено, ₸" required optionalMark={false} hint="Сколько ушло поставщику. Подставлена сумма заявки — поправьте, если оплатили другую.">
                        <AmountInput value={fields.paid_amount ?? 0} onChange={(value) => set('paid_amount', value)} ariaLabel="Оплаченная сумма" />
                    </Field>
                </div>
            )}

            {no === 11 && (
                <div className="flex flex-wrap gap-x-6 gap-y-3">
                    <Field as="div" label="Что получили" required optionalMark={false} hint={HINTS.received}>
                        <Choice value={receipt.received} options={RECEIVED_OPTIONS} onChange={(value) => setReceipt((prev) => ({ ...prev, received: value }))} ariaLabel="Что получили" />
                    </Field>
                    <Field as="div" label="Оригинал документа" required optionalMark={false} hint={HINTS.originals}>
                        <Choice value={receipt.originals} options={ORIGINALS_OPTIONS} onChange={(value) => setReceipt((prev) => ({ ...prev, originals: value }))} ariaLabel="Оригинал документа" />
                    </Field>
                </div>
            )}

            {takesFiles && (
                <div className="space-y-1.5">
                    <div className="flex items-center gap-1.5 px-1 text-[11px] font-semibold uppercase tracking-wider text-slate-500">
                        {no === 1 ? 'Документы к закупу' : (no === 7 ? 'Счёт поставщика' : (no === 11 ? 'АВР или накладная' : 'Файлы шага'))}
                        {step.files_required && <span className="font-normal normal-case tracking-normal text-slate-400">обязательно</span>}
                        {no === 10 && (request.needs_payment_order || request.needs_power_of_attorney) && (
                            <span className="font-normal normal-case tracking-normal text-slate-400">
                                на шаге 7 просили: {[request.needs_payment_order && 'платёжное поручение', request.needs_power_of_attorney && 'доверенность'].filter(Boolean).join(', ')}
                            </span>
                        )}
                    </div>
                    <AttachmentList attachments={ownFiles} apiBaseUrl={apiBaseUrl} headers={headers} canRemove={canRemoveAttachment} onRemove={onRemoveAttachment} showToast={showToast} />
                    <FilePicker files={files} onChange={setFiles} kinds={fileKinds} />
                </div>
            )}

            <Field label="Комментарий">
                <textarea className={`${iosInput} min-h-[56px] resize-y`} value={comment} onChange={(event) => setComment(event.target.value)} maxLength={4000} placeholder="Что важно знать следующему по маршруту" />
            </Field>

            {missing.length > 0 && (
                <div className="px-1 text-[12.5px] text-slate-500">
                    Чтобы продолжить: {missing.join('; ')}.
                </div>
            )}
            <div className="flex flex-wrap items-center gap-2">
                <button type="button" className={iosBtnPrimary} disabled={busy || missing.length > 0} onClick={complete}>
                    {busy && <Loader2 size={14} className="animate-spin" />}
                    {step.action || 'Отписаться'}
                </button>
                {no === 1 && permissions.can_edit && (
                    <button type="button" className={iosBtnSecondary} disabled={busy} onClick={onEdit}>
                        <Pencil size={14} /> Изменить заявку
                    </button>
                )}
                {permissions.can_return && (
                    <button type="button" className={iosBtnSecondary} disabled={busy} onClick={() => { setMode(mode === 'return' ? null : 'return'); setReason(''); }}>
                        Вернуть на шаг {step.returns_to}
                    </button>
                )}
                {permissions.can_reject && (
                    <button type="button" className={`${iosBtnGhost} text-rose-600 hover:bg-rose-50`} disabled={busy} onClick={() => { setMode(mode === 'reject' ? null : 'reject'); setReason(''); }}>
                        Отклонить
                    </button>
                )}
            </div>
            {(mode === 'return' || mode === 'reject') && (
                <div className="space-y-2 rounded-xl bg-slate-50 p-3">
                    <div className="text-[12.5px] text-slate-600">
                        {mode === 'return'
                            ? `Заявка вернётся инициатору на шаг ${step.returns_to}. Напишите, что исправить, — он увидит это над своим шагом.`
                            : 'Заявка будет закрыта как отклонённая. Укажите причину — её увидит инициатор.'}
                    </div>
                    <textarea className={`${iosInput} min-h-[56px] resize-y`} value={reason} onChange={(event) => setReason(event.target.value)} placeholder={mode === 'return' ? 'Что исправить' : 'Причина отклонения'} maxLength={4000} />
                    <div className="flex gap-2">
                        <button type="button" className={mode === 'reject' ? `${iosBtnPrimary} bg-rose-600 hover:bg-rose-700` : iosBtnPrimary} disabled={busy || !reason.trim()} onClick={() => (mode === 'return' ? onReturn(reason.trim()) : onReject(reason.trim()))}>
                            {mode === 'return' ? 'Вернуть' : 'Отклонить заявку'}
                        </button>
                        <button type="button" className={iosBtnSecondary} onClick={() => setMode(null)}>Отмена</button>
                    </div>
                </div>
            )}
        </div>
    );
};

export default PaymentStepForm;
