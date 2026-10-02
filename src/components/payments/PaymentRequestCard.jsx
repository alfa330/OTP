import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import { Ban, Check, ChevronDown, Loader2, Pencil, RotateCcw, Trash2, UserRoundCog, X } from 'lucide-react';
import { iosBtnGhost, iosBtnPrimary, iosBtnSecondary, iosCard, iosInput, IosMenu, IosModal } from '../ui/ios';
import IosDatePicker from '../ui/DatePicker';
import CustomSelect from '../ui/CustomSelect';
import InfoHint from '../common/InfoHint';
import {
    ROLE_LABELS, SOURCE_META, TYPE_META, approverBasisLine, attachmentKindsForStep, cardNumberLabel, describeEvent,
    dueLabel, fmtDate, fmtDateTime, fmtMoney, fmtQty, isClosed, netAmount, passedStepsLabel, requestState, routeSummary,
    splitRoute, tonePill,
} from './paymentsMeta';
import {
    AmountInput, AttachmentList, ErrorBox, Field, FilePicker, NoticeBox, Row, SectionTitle, StatePill, UserSelect,
    appendPayloadFiles, errorText,
} from './paymentsUi';

/*
 * Карточка заявки: где она сейчас и что нужно сделать, маршрут из 12 шагов,
 * реквизиты, документы, история.
 *
 * Главное в карточке — ТЕКУЩИЙ ШАГ. Постановка Зарины вся про маршрут: «на
 * каждом шаге свой ответственный, следующий шаг открывается только после
 * отписки предыдущего». Человек открывает карточку, чтобы отписаться на своём
 * шаге, поэтому пройденная голова маршрута свёрнута в одну строку (последний
 * пройденный шаг виден — «что было только что»), текущий раскрыт с формой
 * отписки, будущие — короткими серыми строками.
 *
 * Действие на шаге — прямо в маршруте, а не отдельным окном: это единственное
 * частое действие над заявкой. Форма показывает ровно то, что нужно на ЭТОМ
 * шаге (реквизиты — на 5-м, счёт и описание — на 7-м, дата и сумма оплаты —
 * на 10-м). Пользователю без права на шаг форма не рисуется вовсе — кнопка,
 * которая всегда отвечает отказом, хуже её отсутствия.
 *
 * Файлы живут в одном месте — «Документы», с подписью шага. В маршруте их нет
 * (кроме текущего шага, где их прикладывают): один и тот же список дважды на
 * экране — шум.
 *
 * Редкие действия (возврат, отмена, удаление) — в меню «…» подвала: на виду
 * только «Изменить» и «Готово».
 */

const dateTrigger = 'flex w-full items-center gap-2 rounded-xl bg-slate-100 px-3.5 py-2.5 '
    + 'text-[14px] tabular-nums text-slate-900 border-0 transition hover:bg-slate-200/70 '
    + 'focus:outline-none focus:ring-2 focus:ring-blue-500/70 [&>span]:flex-1 [&>span]:text-left';

const EVENTS_SHOWN = 6;
const PREVIEW_DEBOUNCE_MS = 300;

const shortDateTime = (value) => {
    const text = fmtDateTime(value);
    return text.length > 10 ? `${text.slice(0, 5)} ${text.slice(11)}` : text;
};

const StepDot = ({ kind, no }) => {
    if (kind === 'done') {
        return (
            <span className="grid h-5 w-5 shrink-0 place-items-center rounded-full bg-emerald-500 text-white">
                <Check size={12} strokeWidth={3} />
            </span>
        );
    }
    if (kind === 'current') {
        return <span className="grid h-6 w-6 shrink-0 place-items-center rounded-full bg-blue-600 text-[12px] font-semibold tabular-nums text-white ring-4 ring-blue-100">{no}</span>;
    }
    if (kind === 'stopped') {
        return (
            <span className="grid h-5 w-5 shrink-0 place-items-center rounded-full bg-rose-500 text-white">
                <X size={12} strokeWidth={3} />
            </span>
        );
    }
    if (kind === 'skipped') {
        return <span className="grid h-5 w-5 shrink-0 place-items-center rounded-full bg-slate-100 text-[11px] text-slate-400">—</span>;
    }
    return <span className="grid h-5 w-5 shrink-0 place-items-center rounded-full bg-white text-[11px] tabular-nums text-slate-400 ring-1 ring-slate-200">{no}</span>;
};

const RouteHint = ({ basis }) => {
    if (!basis) return null;
    const summary = routeSummary(basis);
    const evaluations = basis.evaluations || [];
    return (
        <InfoHint title="Основание">
            <div className="space-y-2 text-[12px] leading-snug">
                <div>
                    Стандартный согласующий: <b>{basis.standard_label || 'Учредитель'}</b>.
                    {' '}Фактический: <b>{summary.approver}</b>.
                </div>
                {evaluations.length === 0 && <div>Действующих Приказов нет — счёт согласует Учредитель.</div>}
                {evaluations.map((item) => (
                    <div key={item.order_id} className="rounded-lg bg-slate-100 px-2 py-1.5">
                        <div className="font-medium">Приказ №{item.number}{item.issued_on ? ` от ${item.issued_on}` : ''} — {item.applies ? 'применён' : 'не применён'}</div>
                        <ul className="mt-0.5 space-y-0.5">
                            {(item.checks || []).map((check) => (
                                <li key={check.key} className={check.ok ? 'text-slate-600' : 'text-rose-700'}>
                                    {check.ok ? '✓' : '✗'} {check.label}: {check.detail}
                                </li>
                            ))}
                        </ul>
                    </div>
                ))}
                {basis.conflict && <div className="text-amber-700">Несколько действующих Приказов дают право разным людям — применён приоритетный.</div>}
            </div>
        </InfoHint>
    );
};

const PaymentRequestCard = ({
    open, onClose, requestId, apiBaseUrl, headers, dictionaries, users, me, onChanged, onEdit, showToast,
}) => {
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');
    const [busy, setBusy] = useState(false);
    const [comment, setComment] = useState('');
    const [fields, setFields] = useState({});
    const [files, setFiles] = useState([]);
    const [extraFiles, setExtraFiles] = useState([]);
    const [mode, setMode] = useState(null);       // 'return' | 'reject' | 'cancel' | 'refund' | 'delete' | 'reassign-<n>'
    const [reassignTo, setReassignTo] = useState(null);
    const [refund, setRefund] = useState({ refund_on: '', refund_amount: 0 });
    const [showFolded, setShowFolded] = useState(false);
    const [showAllEvents, setShowAllEvents] = useState(false);
    const [preview, setPreview] = useState(null);
    const modePanelRef = useRef(null);

    const toastRef = useRef(showToast);
    useEffect(() => { toastRef.current = showToast; }, [showToast]);

    const load = useCallback(async () => {
        if (!requestId) return;
        setLoading(true);
        setError('');
        try {
            const response = await axios.get(`${apiBaseUrl}/api/payments/requests/${requestId}`, { headers: headers() });
            setData(response.data);
        } catch (err) {
            setError(errorText(err, 'Не удалось загрузить заявку'));
        } finally {
            setLoading(false);
        }
    }, [apiBaseUrl, headers, requestId]);

    useEffect(() => {
        if (!open) { setData(null); setMode(null); return; }
        setComment('');
        setFields({});
        setFiles([]);
        setExtraFiles([]);
        setMode(null);
        setShowFolded(false);
        setShowAllEvents(false);
        setPreview(null);
        load();
    }, [open, load]);

    /* Панель редкого действия открывается из меню в подвале — подкручиваем к ней. */
    useEffect(() => {
        if (mode && !mode.startsWith('reassign')) modePanelRef.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    }, [mode]);

    const request = data?.request;
    const steps = useMemo(() => data?.steps || [], [data]);
    const permissions = data?.permissions || {};
    const active = request?.status === 'active';
    const current = useMemo(() => (active ? steps.find((step) => step.state === 'current') : null), [steps, active]);
    const state = requestState(request);
    const route = useMemo(() => splitRoute(steps), [steps]);

    // Поля шага заполняются значениями заявки, чтобы человек видел и правил
    // текущее, а не пустое поле поверх уже введённого.
    useEffect(() => {
        if (!request || !current) return;
        const next = {};
        if (current.step_no === 5) next.invoice_requisites = request.invoice_requisites || '';
        if (current.step_no === 7) {
            next.invoice_description = request.invoice_description || '';
            next.invoice_number = request.invoice_number || '';
            next.invoice_date = request.invoice_date ? String(request.invoice_date).slice(0, 10) : '';
            next.legal_entity_id = request.legal_entity_id ?? null;
            next.contract_id = request.contract_id ?? null;
            next.needs_power_of_attorney = Boolean(request.needs_power_of_attorney);
            next.needs_payment_order = Boolean(request.needs_payment_order);
        }
        if (current.step_no === 8) next.previous_payment_note = request.previous_payment_note || '';
        if (current.step_no === 10) {
            next.paid_on = request.paid_on ? String(request.paid_on).slice(0, 10) : '';
            next.paid_amount = request.paid_amount ?? request.amount ?? 0;
        }
        setFields(next);
    }, [request, current]);

    /* Шаг 7: ещё ДО отправки видно, кто согласует счёт (Учредитель или
       согласующий по Приказу) и не остановит ли его правило договора. Сервер
       считает тем же кодом, что и при отписке, — расхождения быть не может. */
    const canAct = Boolean(permissions.can_act);
    const previewContract = fields.contract_id ?? null;
    const previewDate = fields.invoice_date || '';
    useEffect(() => {
        if (!request || current?.step_no !== 7 || !canAct) { setPreview(null); return undefined; }
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
    }, [apiBaseUrl, headers, request, current?.step_no, canAct, previewContract, previewDate]);

    const apply = useCallback((response) => {
        const next = response?.data;
        if (next?.request) {
            setData(next);
            onChanged?.(next.request);
        }
        if (next?.warnings?.length) toastRef.current?.(next.warnings.join('. '), 'info');
    }, [onChanged]);

    const run = useCallback(async (fn, successText) => {
        setBusy(true);
        setError('');
        try {
            const response = await fn();
            apply(response);
            if (successText) toastRef.current?.(successText, 'success');
            setMode(null);
            setComment('');
            setFiles([]);
            setExtraFiles([]);
            return true;
        } catch (err) {
            const payload = err?.response?.data;
            if (payload?.request) { setData(payload); onChanged?.(payload.request); }
            setError(errorText(err, 'Действие не выполнено'));
            return false;
        } finally {
            setBusy(false);
        }
    }, [apply, onChanged]);

    const complete = () => run(() => {
        const form = new FormData();
        form.append('payload', JSON.stringify({ ...fields, comment }));
        appendPayloadFiles(form, files);
        return axios.post(`${apiBaseUrl}/api/payments/requests/${requestId}/steps/${current.step_no}/complete`, form, { headers: headers() });
    }, 'Шаг отписан');

    const returnStep = () => run(() => axios.post(
        `${apiBaseUrl}/api/payments/requests/${requestId}/steps/${current.step_no}/return`, { comment }, { headers: headers() },
    ), 'Заявка возвращена на доработку');

    const rejectStep = () => run(() => axios.post(
        `${apiBaseUrl}/api/payments/requests/${requestId}/steps/${current.step_no}/reject`, { comment }, { headers: headers() },
    ), 'Заявка отклонена');

    const cancel = () => run(() => axios.post(
        `${apiBaseUrl}/api/payments/requests/${requestId}/cancel`, { comment }, { headers: headers() },
    ), 'Заявка отменена');

    const reassign = (stepNo, userId) => run(() => axios.post(
        `${apiBaseUrl}/api/payments/requests/${requestId}/steps/${stepNo}/assignee`,
        { user_id: userId, reason: comment }, { headers: headers() },
    ), 'Ответственный изменён');

    const saveRefund = () => run(() => axios.post(
        `${apiBaseUrl}/api/payments/requests/${requestId}/refund`, { ...refund, comment }, { headers: headers() },
    ), refund.refund_amount > 0 ? 'Возврат отмечен' : 'Возврат снят');

    const removeAttachment = (attachment) => run(() => axios.delete(
        `${apiBaseUrl}/api/payments/requests/${requestId}/attachments/${attachment.id}`, { headers: headers() },
    ), 'Файл снят');

    const addFiles = () => run(() => {
        const form = new FormData();
        form.append('step_no', String(current?.step_no || ''));
        appendPayloadFiles(form, extraFiles);
        return axios.post(`${apiBaseUrl}/api/payments/requests/${requestId}/attachments`, form, { headers: headers() });
    }, 'Файлы добавлены');

    const remove = async () => {
        const ok = await run(() => axios.delete(`${apiBaseUrl}/api/payments/requests/${requestId}`, { headers: headers() }), 'Заявка удалена');
        if (ok) { onChanged?.({ id: requestId, deleted: true }); onClose?.(); }
    };

    const legalEntityOptions = useMemo(() => (dictionaries?.legal_entities || []).map((item) => ({ value: item.id, label: item.name })), [dictionaries]);
    const contractOptions = useMemo(() => (dictionaries?.contracts || [])
        .filter((item) => item.counterparty_id === request?.counterparty_id)
        .map((item) => ({
            value: item.id,
            label: `№${item.number}${item.ends_on ? ` до ${fmtDate(item.ends_on)}` : ''}${item.status !== 'active' ? ' · недействующий' : ''}`,
        })), [dictionaries, request?.counterparty_id]);

    const attachmentsByStep = useMemo(() => {
        const map = {};
        (data?.attachments || []).forEach((item) => { (map[item.step_no || 0] ||= []).push(item); });
        return map;
    }, [data]);

    const canRemoveAttachment = (attachment) => permissions.can_delete
        || (attachment.uploaded_by === me?.id && current && attachment.step_no === current.step_no);

    const stepFileKinds = useMemo(() => attachmentKindsForStep(current), [current]);
    const stepTakesFiles = Boolean(current && (current.files?.length > 0 || current.step_no === 1));

    const renderStepForm = () => {
        if (!current || !permissions.can_act) return null;
        const no = current.step_no;
        const missingFiles = current.files_required && !(attachmentsByStep[no] || []).length && !files.length;
        const previewReason = no === 7 && preview?.contract_check && !preview.contract_check.ok ? preview.contract_check.reason : '';
        const previewApprover = no === 7 && preview?.route ? routeSummary(preview.route) : null;
        return (
            <div className="mt-2.5 space-y-3 rounded-2xl bg-white p-3.5 ring-1 ring-blue-100">
                {permissions.acting_as_admin && (
                    <div className="text-[12px] text-slate-500">
                        Вы отписываетесь за ответственного ({current.assignee_name || current.role_label}) как администратор раздела — это будет видно в истории.
                    </div>
                )}
                {/* Блок «ожидает договор» на 7-м шаге показывает предпросмотр —
                    он свежее записанной причины и учитывает выбранный сейчас договор. */}
                {request.block_code && no !== 7 && <NoticeBox text={request.block_reason} />}
                {previewReason && <NoticeBox text={previewReason} />}
                {no === 5 && (
                    <Field label="Реквизиты для счёта" required optionalMark={false} hint="Юр. лицо, БИН, банк, IBAN — то, на что поставщик выставит счёт.">
                        <textarea className={`${iosInput} min-h-[96px] resize-y`} value={fields.invoice_requisites || ''} onChange={(event) => setFields((prev) => ({ ...prev, invoice_requisites: event.target.value }))} maxLength={4000} />
                    </Field>
                )}
                {no === 7 && (
                    <>
                        <Field label="Описание счёта" required optionalMark={false} hint="Одним текстом: что закупается, за какой период, оплата с какого на какое юр. лицо, сумма, отдел.">
                            <textarea className={`${iosInput} min-h-[96px] resize-y`} value={fields.invoice_description || ''} onChange={(event) => setFields((prev) => ({ ...prev, invoice_description: event.target.value }))} maxLength={4000} />
                        </Field>
                        <div className="grid gap-3 sm:grid-cols-2">
                            <Field label="Номер счёта">
                                <input className={iosInput} value={fields.invoice_number || ''} onChange={(event) => setFields((prev) => ({ ...prev, invoice_number: event.target.value }))} maxLength={100} />
                            </Field>
                            <Field label="Дата счёта" hint="По ней проверяется действие договора и Приказа.">
                                <IosDatePicker value={fields.invoice_date || ''} onChange={(value) => setFields((prev) => ({ ...prev, invoice_date: value || '' }))} allowEmpty placeholder="Сегодня" triggerClassName={dateTrigger} ariaLabel="Дата счёта" />
                            </Field>
                        </div>
                        <div className="grid gap-3 sm:grid-cols-2">
                            <Field label="Юр. лицо плательщика" required optionalMark={false}>
                                <CustomSelect value={fields.legal_entity_id ?? null} onChange={(value) => setFields((prev) => ({ ...prev, legal_entity_id: value }))} options={legalEntityOptions} placeholder={legalEntityOptions.length ? 'Выберите' : 'Справочник пуст — заведите юр. лица'} variant="ios" ariaLabel="Юр. лицо" />
                            </Field>
                            <Field label="Договор" hint={`Обязателен, если сумма выше ${fmtMoney(data?.contract_check?.threshold || 300000)}.`}>
                                <CustomSelect value={fields.contract_id ?? null} onChange={(value) => setFields((prev) => ({ ...prev, contract_id: value }))} options={[{ value: null, label: 'Без договора' }, ...contractOptions]} placeholder="Без договора" variant="ios" ariaLabel="Договор" />
                            </Field>
                        </div>
                        <div className="flex flex-wrap gap-4 px-1 text-[13px] text-slate-700">
                            <label className="inline-flex items-center gap-2">
                                <input type="checkbox" className="h-4 w-4 rounded border-slate-300 text-blue-600" checked={Boolean(fields.needs_power_of_attorney)} onChange={(event) => setFields((prev) => ({ ...prev, needs_power_of_attorney: event.target.checked }))} />
                                Нужна доверенность
                            </label>
                            <label className="inline-flex items-center gap-2">
                                <input type="checkbox" className="h-4 w-4 rounded border-slate-300 text-blue-600" checked={Boolean(fields.needs_payment_order)} onChange={(event) => setFields((prev) => ({ ...prev, needs_payment_order: event.target.checked }))} />
                                Нужно платёжное поручение
                            </label>
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
                        {(data?.history || []).length > 0 && (
                            <NoticeBox tone="blue" text="">
                                <div className="text-[11px] font-semibold uppercase tracking-wider text-blue-700/80">Ранее платили</div>
                                {data.history.slice(0, 3).map((row) => (
                                    <div key={row.request_id} className="mt-0.5 flex flex-wrap items-baseline justify-between gap-x-3 text-[12.5px]">
                                        <span className="min-w-0 flex-1 truncate text-blue-900">№{row.request_id} · {row.expense_name}</span>
                                        <span className="tabular-nums text-blue-900/80">{fmtDate(row.paid_on)} · {fmtMoney(row.paid_amount ?? row.amount)}</span>
                                    </div>
                                ))}
                            </NoticeBox>
                        )}
                        <Field label="Проверка счёта" required optionalMark={false} hint="Когда и на какую сумму оплачивали этому контрагенту в последний раз — или что оплат не было.">
                            <textarea className={`${iosInput} min-h-[72px] resize-y`} value={fields.previous_payment_note || ''} onChange={(event) => setFields((prev) => ({ ...prev, previous_payment_note: event.target.value }))} maxLength={4000} />
                        </Field>
                    </>
                )}
                {no === 10 && (
                    <div className="grid gap-3 sm:grid-cols-2">
                        <Field label="Дата оплаты" required optionalMark={false}>
                            <IosDatePicker value={fields.paid_on || ''} onChange={(value) => setFields((prev) => ({ ...prev, paid_on: value || '' }))} allowEmpty placeholder="Сегодня" triggerClassName={dateTrigger} ariaLabel="Дата оплаты" />
                        </Field>
                        <Field label="Оплачено" required optionalMark={false}>
                            <AmountInput value={fields.paid_amount ?? 0} onChange={(value) => setFields((prev) => ({ ...prev, paid_amount: value }))} ariaLabel="Оплаченная сумма" />
                        </Field>
                    </div>
                )}
                {stepTakesFiles && (
                    <div className="space-y-1.5">
                        <div className="flex items-center gap-1.5 px-1 text-[11px] font-semibold uppercase tracking-wider text-slate-500">
                            Файлы шага
                            {current.files_required && <span className="font-normal normal-case tracking-normal text-slate-400">обязательно</span>}
                            {no === 10 && (request.needs_payment_order || request.needs_power_of_attorney) && (
                                <span className="font-normal normal-case tracking-normal text-slate-400">
                                    просили: {[request.needs_payment_order && 'платёжное поручение', request.needs_power_of_attorney && 'доверенность'].filter(Boolean).join(', ')}
                                </span>
                            )}
                        </div>
                        <AttachmentList attachments={attachmentsByStep[no]} apiBaseUrl={apiBaseUrl} headers={headers} canRemove={canRemoveAttachment} onRemove={removeAttachment} showToast={showToast} />
                        <FilePicker files={files} onChange={setFiles} kinds={stepFileKinds} />
                    </div>
                )}
                <Field label="Комментарий">
                    <textarea className={`${iosInput} min-h-[56px] resize-y`} value={comment} onChange={(event) => setComment(event.target.value)} maxLength={4000} placeholder="Отписка: что сделано, на что обратить внимание" />
                </Field>
                <div className="flex flex-wrap items-center gap-2 pt-1">
                    <button type="button" className={iosBtnPrimary} disabled={busy || missingFiles} onClick={complete} title={missingFiles ? 'Сначала приложите файл' : undefined}>
                        {busy && <Loader2 size={14} className="animate-spin" />}
                        {current.action || 'Отписаться'}
                    </button>
                    {permissions.can_return && (
                        <button type="button" className={iosBtnSecondary} disabled={busy} onClick={() => setMode(mode === 'return' ? null : 'return')}>
                            Вернуть на шаг {current.returns_to}
                        </button>
                    )}
                    {permissions.can_reject && (
                        <button type="button" className={`${iosBtnGhost} text-rose-600 hover:bg-rose-50`} disabled={busy} onClick={() => setMode(mode === 'reject' ? null : 'reject')}>
                            Отклонить
                        </button>
                    )}
                </div>
                {(mode === 'return' || mode === 'reject') && (
                    <div className="space-y-2 rounded-xl bg-slate-50 p-3">
                        <div className="text-[12.5px] text-slate-600">
                            {mode === 'return'
                                ? `Заявка вернётся инициатору на шаг ${current.returns_to}. Напишите, что исправить.`
                                : 'Заявка будет закрыта как отклонённая. Укажите причину — её увидит инициатор.'}
                        </div>
                        <textarea className={`${iosInput} min-h-[56px] resize-y`} value={comment} onChange={(event) => setComment(event.target.value)} placeholder="Причина" maxLength={4000} />
                        <div className="flex gap-2">
                            <button type="button" className={mode === 'reject' ? `${iosBtnPrimary} bg-rose-600 hover:bg-rose-700` : iosBtnPrimary} disabled={busy || !comment.trim()} onClick={mode === 'return' ? returnStep : rejectStep}>
                                {mode === 'return' ? 'Вернуть' : 'Отклонить заявку'}
                            </button>
                            <button type="button" className={iosBtnSecondary} onClick={() => setMode(null)}>Отмена</button>
                        </div>
                    </div>
                )}
            </div>
        );
    };

    /* Строка шага маршрута. Пройденный — одна строка «что · кто · когда» и
       комментарий второй строкой; текущий — раскрыт с формой; будущий — серым,
       с тем, кому он достанется. */
    const renderStep = (step, isLast) => {
        const stoppedHere = !active && step.state === 'current';
        const isCurrent = active && step.state === 'current';
        const kind = stoppedHere ? 'stopped' : (isCurrent ? 'current' : step.state);
        const basis = step.step_no === 9 ? request.route_basis : null;
        const basisLine = basis ? approverBasisLine(basis) : '';
        const roleStep = step.role_code !== 'initiator' && step.role_code !== 'manager';
        const who = step.assignee_name || step.role_label;
        let right = '';
        if (step.state === 'done' && step.done_at) right = `${step.done_by_name || ''} · ${shortDateTime(step.done_at)}`;
        else if (step.state === 'pending' || stoppedHere) right = who;
        // У пройденного шага справа — кто отписался. Второй строкой — чей это был
        // шаг, но только если это не очевидно: отписался не сам ответственный
        // (администратор за него) или шаг принадлежал роли.
        let doneSub = '';
        if (step.state === 'done') {
            if (step.assignee_name && step.done_by_name && step.done_by_name !== step.assignee_name) doneSub = `за ${step.assignee_name}`;
            else if (roleStep && !step.assignee_name) doneSub = step.role_label;
        }
        const doneComment = step.state === 'done' ? step.comment : '';
        const reassignOpen = mode === `reassign-${step.step_no}`;
        return (
            <li key={step.step_no} className="group relative flex gap-3">
                <div className="flex w-6 shrink-0 flex-col items-center pt-px">
                    <StepDot kind={kind} no={step.step_no} />
                    {!isLast && <div className={`mt-1 w-px flex-1 ${step.state === 'done' ? 'bg-emerald-200' : 'bg-slate-200'}`} />}
                </div>
                <div className={`min-w-0 flex-1 ${isLast ? '' : 'pb-3'}`}>
                    <div className="flex items-baseline justify-between gap-3">
                        <div className={`min-w-0 text-[13.5px] ${isCurrent ? 'font-semibold text-slate-900' : (step.state === 'done' ? 'text-slate-800' : 'text-slate-500')}`}>
                            {step.title}
                        </div>
                        <div className="flex max-w-[55%] shrink-0 items-baseline gap-1.5">
                            {/* «Сменить» у будущего шага — по наведению на строку, а не
                                постоянно: двенадцать одинаковых ссылок были бы шумом. */}
                            {(step.state === 'pending' || step.state === 'skipped') && permissions.can_reassign && (
                                <button
                                    type="button"
                                    aria-label="Сменить ответственного"
                                    title="Сменить ответственного"
                                    className={`self-center rounded-full p-0.5 text-blue-600 transition hover:bg-blue-50 ${reassignOpen ? '' : 'opacity-0 group-hover:opacity-100 focus:opacity-100'}`}
                                    onClick={() => { setMode(reassignOpen ? null : `reassign-${step.step_no}`); setReassignTo(step.assignee_id ?? null); }}
                                >
                                    <UserRoundCog size={13} />
                                </button>
                            )}
                            {right && <div className="truncate text-right text-[12px] tabular-nums text-slate-500">{right}</div>}
                        </div>
                    </div>
                    {step.state === 'skipped' && <div className="text-[12.5px] text-slate-400">{step.comment || 'Шаг пропущен'}</div>}
                    {stoppedHere && (
                        <div className="text-[12.5px] text-rose-700">
                            {request.status === 'rejected' ? 'Заявка отклонена на этом шаге' : 'Заявка отменена на этом шаге'}
                        </div>
                    )}
                    {(doneSub || doneComment) && (
                        <div className="text-[12.5px] text-slate-500">
                            {doneSub}
                            {doneSub && doneComment ? ' — ' : ''}
                            {doneComment ? <span className="whitespace-pre-line text-slate-600">{doneComment}</span> : null}
                        </div>
                    )}
                    {isCurrent && (
                        <div className="text-[12.5px] text-slate-600">
                            <span className="text-slate-900">{who}</span>
                            {/* Основание по Приказу само говорит, кого он замещает, —
                                «вместо роли» рядом с ним было бы повтором. */}
                            {roleStep && step.assignee_id && !basisLine && <span className="text-slate-400"> · вместо роли «{step.role_label}»</span>}
                            {permissions.can_reassign && (
                                <button type="button" className="ml-2 inline-flex items-center gap-1 text-[12px] text-blue-600 transition hover:underline" onClick={() => { setMode(reassignOpen ? null : `reassign-${step.step_no}`); setReassignTo(step.assignee_id ?? null); }}>
                                    <UserRoundCog size={12} /> сменить
                                </button>
                            )}
                            {/* Описание шага нужно тому, кто ждёт, и на шагах без полей;
                                у формы с полями то же самое сказано подсказками полей. */}
                            {step.brief && !basisLine && !(permissions.can_act && step.fields?.length) && <div className="mt-0.5 text-slate-500">{step.brief}</div>}
                        </div>
                    )}
                    {/* Основание согласующего счёта (пп. 11–12 ТЗ о Приказах) — открытым
                        текстом, а разбивка по условиям — под «i». */}
                    {basisLine && (
                        <div className="mt-0.5 text-[12.5px] text-slate-500">
                            {basisLine}
                            <span className="ml-1 inline-flex align-middle"><RouteHint basis={basis} /></span>
                        </div>
                    )}
                    {reassignOpen && (
                        <div className="mt-2 space-y-2 rounded-xl bg-slate-50 p-3">
                            <UserSelect users={users} value={reassignTo} onChange={setReassignTo} placeholder="Кому передать шаг" />
                            <input className={`${iosInput} py-2 text-[13px]`} value={comment} onChange={(event) => setComment(event.target.value)} placeholder="Причина (необязательно)" maxLength={4000} />
                            <div className="flex flex-wrap gap-2">
                                <button type="button" className={iosBtnPrimary} disabled={busy || !reassignTo} onClick={() => reassign(step.step_no, reassignTo)}>Передать</button>
                                {roleStep && step.assignee_id && (
                                    <button type="button" className={iosBtnSecondary} disabled={busy} onClick={() => reassign(step.step_no, null)}>Вернуть роли «{step.role_label}»</button>
                                )}
                                <button type="button" className={iosBtnGhost} onClick={() => setMode(null)}>Отмена</button>
                            </div>
                        </div>
                    )}
                    {isCurrent && (
                        permissions.can_act
                            ? renderStepForm()
                            : (
                                <div className="mt-1.5 text-[12.5px] text-slate-500">
                                    Ждём отписки: {who}.
                                    {request.block_code && <div className="mt-1 text-amber-700">{request.block_reason}</div>}
                                </div>
                            )
                    )}
                </div>
            </li>
        );
    };

    const menuItems = request ? [
        permissions.can_refund && {
            key: 'refund', label: request.refund_amount > 0 ? 'Изменить возврат' : 'Отметить возврат', icon: RotateCcw,
            onSelect: () => { setRefund({ refund_on: request.refund_on ? String(request.refund_on).slice(0, 10) : '', refund_amount: request.refund_amount || 0 }); setMode('refund'); },
        },
        permissions.can_cancel && { key: 'cancel', label: 'Отменить заявку', icon: Ban, onSelect: () => setMode('cancel') },
        permissions.can_delete && { key: 'delete', label: 'Удалить', icon: Trash2, danger: true, separatorBefore: Boolean(permissions.can_refund || permissions.can_cancel), onSelect: () => setMode('delete') },
    ] : [];

    const footer = request ? (
        <>
            <div className="mr-auto">
                <IosMenu items={menuItems} label="Ещё действия" align="left" disabled={busy} />
            </div>
            {permissions.can_edit && (
                <button type="button" className={iosBtnSecondary} disabled={busy} onClick={() => onEdit?.(request, data.items)}>
                    <Pencil size={14} /> Изменить
                </button>
            )}
            <button type="button" className={iosBtnPrimary} onClick={onClose}>Готово</button>
        </>
    ) : null;

    const events = (data?.events || []).slice().reverse();
    const shownEvents = showAllEvents ? events : events.slice(0, EVENTS_SHOWN);
    const canAddFiles = (permissions.can_edit || permissions.can_act) && !isClosed(request);
    // Файлы на текущем шаге прикладываются в его форме; здесь — только если
    // у шага своего места для файлов нет.
    const showExtraPicker = canAddFiles && !(permissions.can_act && stepTakesFiles);

    return (
        <IosModal
            open={open}
            onClose={onClose}
            title={request ? `Заявка №${request.id}` : 'Заявка'}
            subtitle={request ? `${fmtDateTime(request.created_at)} · ${request.initiator_name || ''}` : ''}
            maxWidth="max-w-3xl"
            footer={footer}
        >
            {loading && !data && (
                <div className="flex items-center justify-center gap-2 py-12 text-[13px] text-slate-500">
                    <Loader2 size={15} className="animate-spin" /> Загружаем заявку…
                </div>
            )}
            {!loading && !data && error && <ErrorBox text={error} />}
            {request && (
                <div className="space-y-4">
                    {/* Сводка: что, сколько, где сейчас. Цвет только у состояний со смыслом. */}
                    <div className={`${iosCard} p-4`}>
                        <div className="flex flex-wrap items-start justify-between gap-3">
                            <div className="min-w-0 flex-1">
                                <div className="text-[16px] font-semibold leading-snug text-slate-900">{request.expense_name}</div>
                                <div className="mt-0.5 text-[13px] text-slate-500">
                                    {request.counterparty_name || 'Контрагент не указан'}
                                    {request.project_name ? ` · ${request.project_name}` : ''}
                                </div>
                            </div>
                            <div className="text-right">
                                <div className="text-[20px] font-semibold tabular-nums text-slate-900">{fmtMoney(netAmount(request))}</div>
                                {request.refund_amount > 0 && (
                                    <div className="text-[12px] tabular-nums text-slate-500">{fmtMoney(request.amount)} − возврат {fmtMoney(request.refund_amount)}</div>
                                )}
                            </div>
                        </div>
                        <div className="mt-3 flex flex-wrap items-center gap-2 text-[12.5px]">
                            <StatePill request={request} />
                            {active && (
                                <span className={`inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 font-medium ${tonePill('current').fill}`}>
                                    Шаг {request.current_step} из 12 · {request.current_assignee_name || ROLE_LABELS[request.current_role] || '—'}
                                </span>
                            )}
                            {request.due_on && (
                                <span className={`rounded-full px-2 py-0.5 ${state === 'overdue' ? tonePill('overdue').fill : 'bg-slate-100 text-slate-600'}`}>
                                    Срок {fmtDate(request.due_on)} · {dueLabel(request)}
                                </span>
                            )}
                        </div>
                        {request.status === 'rejected' && request.rejected_reason && (
                            <div className="mt-3 text-[13px] text-slate-700"><span className="text-slate-500">Причина отклонения:</span> {request.rejected_reason}</div>
                        )}
                        {request.status === 'cancelled' && request.rejected_reason && (
                            <div className="mt-3 text-[13px] text-slate-700"><span className="text-slate-500">Почему отменена:</span> {request.rejected_reason}</div>
                        )}
                    </div>

                    <ErrorBox text={data && error} />

                    <div ref={modePanelRef}>
                        {mode === 'cancel' && (
                            <div className="space-y-2 rounded-2xl bg-slate-50 p-3.5 ring-1 ring-slate-200">
                                <div className="text-[13px] text-slate-700">Заявка будет закрыта как отменённая. Оплаты по ней не было.</div>
                                <textarea className={`${iosInput} min-h-[56px] resize-y`} value={comment} onChange={(event) => setComment(event.target.value)} placeholder="Почему отменяем (необязательно)" maxLength={4000} />
                                <div className="flex gap-2">
                                    <button type="button" className={iosBtnPrimary} disabled={busy} onClick={cancel}>Отменить заявку</button>
                                    <button type="button" className={iosBtnSecondary} onClick={() => setMode(null)}>Назад</button>
                                </div>
                            </div>
                        )}
                        {mode === 'delete' && (
                            <div className="space-y-2 rounded-2xl bg-rose-50 p-3.5 ring-1 ring-rose-200">
                                <div className="text-[13px] text-rose-800">Удалить заявку вместе с историей и файлами? Это не отменить. Обычно достаточно «Отменить заявку» — она останется в реестре.</div>
                                <div className="flex gap-2">
                                    <button type="button" className={`${iosBtnPrimary} bg-rose-600 hover:bg-rose-700`} disabled={busy} onClick={remove}>Удалить навсегда</button>
                                    <button type="button" className={iosBtnSecondary} onClick={() => setMode(null)}>Назад</button>
                                </div>
                            </div>
                        )}
                        {mode === 'refund' && (
                            <div className="space-y-3 rounded-2xl bg-slate-50 p-3.5 ring-1 ring-slate-200">
                                <div className="text-[13px] text-slate-700">Возврат средств: итоговая сумма расхода пересчитается как сумма − возврат.</div>
                                <div className="grid gap-3 sm:grid-cols-2">
                                    <Field label="Дата возврата" required optionalMark={false}>
                                        <IosDatePicker value={refund.refund_on} onChange={(value) => setRefund((prev) => ({ ...prev, refund_on: value || '' }))} allowEmpty placeholder="Дата" triggerClassName={dateTrigger} ariaLabel="Дата возврата" />
                                    </Field>
                                    <Field label="Сумма возврата" required optionalMark={false} hint="Полная или частичная. Ноль — снять возврат.">
                                        <AmountInput value={refund.refund_amount} onChange={(value) => setRefund((prev) => ({ ...prev, refund_amount: value }))} ariaLabel="Сумма возврата" />
                                    </Field>
                                </div>
                                <div className="flex gap-2">
                                    <button type="button" className={iosBtnPrimary} disabled={busy || (refund.refund_amount > 0 && !refund.refund_on)} onClick={saveRefund}>Сохранить</button>
                                    <button type="button" className={iosBtnSecondary} onClick={() => setMode(null)}>Назад</button>
                                </div>
                            </div>
                        )}
                    </div>

                    {/* Маршрут */}
                    <section className="space-y-1.5">
                        <SectionTitle>Маршрут согласования</SectionTitle>
                        <div className={`${iosCard} p-3.5`}>
                            {route.folded.length > 0 && !showFolded && (
                                <button
                                    type="button"
                                    onClick={() => setShowFolded(true)}
                                    className="group mb-3 flex w-full items-center gap-3 rounded-xl text-left"
                                >
                                    <span className="flex w-6 shrink-0 justify-center"><StepDot kind="done" /></span>
                                    <span className="min-w-0 flex-1 text-[13px] text-slate-500 transition group-hover:text-slate-800">
                                        Ещё {passedStepsLabel(route.folded.length)}
                                    </span>
                                    <span className="inline-flex shrink-0 items-center gap-0.5 text-[12px] font-medium text-blue-600">
                                        Показать <ChevronDown size={13} />
                                    </span>
                                </button>
                            )}
                            <ol>
                                {(showFolded ? steps : route.visible).map((step, index, list) => renderStep(step, index === list.length - 1))}
                            </ol>
                        </div>
                    </section>

                    {/* Заявка */}
                    <section className="space-y-1.5">
                        <SectionTitle>Заявка</SectionTitle>
                        <div className={`${iosCard} px-4 py-2`}>
                            <Row label="Позиции">
                                <div className="space-y-0.5">
                                    {(data.items || []).map((item) => (
                                        <div key={item.id} className="flex flex-wrap items-baseline justify-between gap-x-3">
                                            <span>{item.name}</span>
                                            <span className="tabular-nums text-slate-600">{fmtQty(item.quantity)}{item.unit ? ` ${item.unit}` : ''} × {fmtMoney(item.unit_price)} = {fmtMoney(item.total)}</span>
                                        </div>
                                    ))}
                                </div>
                            </Row>
                            <Row label="Категория">{[request.category_name, request.subcategory_name].filter(Boolean).join(' → ')}</Row>
                            <Row label="Контрагент">{request.counterparty_name}{request.counterparty_bin ? ` · БИН ${request.counterparty_bin}` : ''}</Row>
                            <Row label="Договор">{request.contract_number ? `№${request.contract_number}` : null}</Row>
                            <Row label="Оплата">{[SOURCE_META[request.payment_source]?.label, TYPE_META[request.payment_type]?.label?.toLowerCase(), request.legal_entity_name && `с ${request.legal_entity_name}`].filter(Boolean).join(' · ')}</Row>
                            <Row label="Период оплаты">{request.payment_period}</Row>
                            <Row label="Филиал / регион">{request.branch}</Row>
                            <Row label="Отдел">{request.department_name}</Row>
                            <Row label="Номер карты">{cardNumberLabel(request.card_number)}</Row>
                            <Row label="Руководитель">{request.manager_name}</Row>
                            <Row label="Примечания">{request.notes}</Row>
                        </div>
                    </section>

                    {(request.invoice_requisites || request.invoice_number || request.invoice_description || request.previous_payment_note || request.paid_on) && (
                        <section className="space-y-1.5">
                            <SectionTitle>Счёт и оплата</SectionTitle>
                            <div className={`${iosCard} px-4 py-2`}>
                                <Row label="Реквизиты">{request.invoice_requisites}</Row>
                                <Row label="Счёт">{request.invoice_number || request.invoice_date ? `${request.invoice_number ? `№${request.invoice_number}` : ''}${request.invoice_date ? ` от ${fmtDate(request.invoice_date)}` : ''}`.trim() : null}</Row>
                                <Row label="Описание счёта">{request.invoice_description}</Row>
                                <Row label="Нужны документы">{[request.needs_payment_order && 'платёжное поручение', request.needs_power_of_attorney && 'доверенность'].filter(Boolean).join(', ') || null}</Row>
                                <Row label="Проверка бухгалтерии">{request.previous_payment_note}</Row>
                                <Row label="Оплачено">{request.paid_on ? `${fmtDate(request.paid_on)} · ${fmtMoney(request.paid_amount)}` : null}</Row>
                                <Row label="Возврат">{request.refund_amount > 0 ? `${fmtDate(request.refund_on)} · ${fmtMoney(request.refund_amount)}` : null}</Row>
                            </div>
                        </section>
                    )}

                    {((data.attachments || []).length > 0 || showExtraPicker) && (
                        <section className="space-y-1.5">
                            <SectionTitle>Документы</SectionTitle>
                            <div className={`${iosCard} p-2`}>
                                <AttachmentList attachments={data.attachments} apiBaseUrl={apiBaseUrl} headers={headers} canRemove={canRemoveAttachment} onRemove={removeAttachment} showToast={showToast} showStep />
                                {showExtraPicker && (
                                    <div className="px-2 pt-1">
                                        <FilePicker files={extraFiles} onChange={setExtraFiles} kinds={stepFileKinds} label="Добавить документ" />
                                        {extraFiles.length > 0 && (
                                            <button type="button" className={`${iosBtnPrimary} mb-1 mt-2`} disabled={busy} onClick={addFiles}>
                                                {busy && <Loader2 size={14} className="animate-spin" />} Загрузить
                                            </button>
                                        )}
                                    </div>
                                )}
                            </div>
                        </section>
                    )}

                    <section className="space-y-1.5">
                        <SectionTitle
                            right={events.length > EVENTS_SHOWN ? (
                                <button type="button" className="px-1 text-[12px] font-medium text-blue-600 hover:underline" onClick={() => setShowAllEvents((prev) => !prev)}>
                                    {showAllEvents ? 'Свернуть' : `Вся история · ${events.length}`}
                                </button>
                            ) : null}
                        >
                            История
                        </SectionTitle>
                        <div className={`${iosCard} px-4 py-2`}>
                            {shownEvents.map((event) => (
                                <div key={event.id} className="flex gap-3 py-1.5 text-[12.5px]">
                                    <div className="w-[118px] shrink-0 tabular-nums text-slate-500">{fmtDateTime(event.created_at)}</div>
                                    <div className="min-w-0 flex-1">
                                        <div className="text-slate-800">{describeEvent(event, steps)}{event.actor_name ? <span className="text-slate-500"> · {event.actor_name}</span> : null}</div>
                                        {event.comment && <div className="mt-0.5 whitespace-pre-line text-slate-600">{event.comment}</div>}
                                    </div>
                                </div>
                            ))}
                        </div>
                    </section>
                </div>
            )}
        </IosModal>
    );
};

export default PaymentRequestCard;
