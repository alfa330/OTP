import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import { Check, Loader2, Pencil, Trash2, UserRoundCog } from 'lucide-react';
import { iosBtnGhost, iosBtnPrimary, iosBtnSecondary, iosCard, iosInput, IosModal } from '../ui/ios';
import IosDatePicker from '../ui/DatePicker';
import CustomSelect from '../ui/CustomSelect';
import InfoHint from '../common/InfoHint';
import {
    ROLE_LABELS, SOURCE_META, TYPE_META, attachmentKindsForStep, cardNumberLabel, describeEvent, dueLabel, fmtDate,
    fmtDateTime, fmtMoney, fmtQty, isClosed, requestState, routeSummary, stateMeta, tonePill,
} from './paymentsMeta';
import {
    AmountInput, AttachmentList, ErrorBox, Field, FilePicker, NoticeBox, Row, SectionTitle, StatePill, UserSelect,
    appendPayloadFiles, errorText,
} from './paymentsUi';

/*
 * Карточка заявки: маршрут из 12 шагов, действие на текущем шаге, реквизиты
 * заявки, файлы, история.
 *
 * Главное в карточке — МАРШРУТ. Постановка Зарины вся про него: «на каждом
 * шаге свой ответственный, следующий шаг открывается только после отписки
 * предыдущего». Поэтому маршрут идёт первым и рисуется лентой сверху вниз:
 * пройденные шаги — галочкой с именем и датой, текущий — раскрыт с формой
 * отписки, будущие — серым. Читается как чек-лист: где заявка и что дальше.
 *
 * Действие на шаге — прямо в ленте, а не отдельным окном: это единственное
 * частое действие над заявкой. Форма показывает ровно то, что нужно на ЭТОМ
 * шаге (реквизиты — на 5-м, счёт и описание — на 7-м, дата и сумма оплаты —
 * на 10-м), а не все поля процесса сразу.
 *
 * Пользователю без права на шаг форма не рисуется вовсе — кнопка, которая
 * всегда отвечает отказом, хуже её отсутствия.
 */

const dateTrigger = 'flex w-full items-center gap-2 rounded-xl bg-slate-100 px-3.5 py-2.5 '
    + 'text-[14px] tabular-nums text-slate-900 border-0 transition hover:bg-slate-200/70 '
    + 'focus:outline-none focus:ring-2 focus:ring-blue-500/70 [&>span]:flex-1 [&>span]:text-left';

const StepDot = ({ state, no }) => {
    if (state === 'done') {
        return (
            <span className="grid h-6 w-6 shrink-0 place-items-center rounded-full bg-emerald-500 text-white">
                <Check size={13} strokeWidth={2.5} />
            </span>
        );
    }
    if (state === 'current') {
        return <span className="grid h-6 w-6 shrink-0 place-items-center rounded-full bg-blue-600 text-[12px] font-semibold tabular-nums text-white ring-4 ring-blue-100">{no}</span>;
    }
    if (state === 'skipped') {
        return <span className="grid h-6 w-6 shrink-0 place-items-center rounded-full bg-slate-100 text-[12px] text-slate-400">—</span>;
    }
    return <span className="grid h-6 w-6 shrink-0 place-items-center rounded-full bg-white text-[12px] tabular-nums text-slate-400 ring-1 ring-slate-200">{no}</span>;
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
                {basis.order_number && <div>{summary.basisText}</div>}
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
    const [mode, setMode] = useState(null);       // 'return' | 'reject' | 'cancel' | 'reassign' | 'refund' | 'delete'
    const [reassignTo, setReassignTo] = useState(null);
    const [refund, setRefund] = useState({ refund_on: '', refund_amount: 0 });

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
        setMode(null);
        load();
    }, [open, load]);

    const request = data?.request;
    const steps = data?.steps || [];
    const permissions = data?.permissions || {};
    const current = useMemo(() => steps.find((step) => step.state === 'current'), [steps]);
    const state = requestState(request);
    const tone = stateMeta(state).tone;

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

    const reassign = (stepNo) => run(() => axios.post(
        `${apiBaseUrl}/api/payments/requests/${requestId}/steps/${stepNo}/assignee`,
        { user_id: reassignTo, reason: comment }, { headers: headers() },
    ), 'Ответственный изменён');

    const saveRefund = () => run(() => axios.post(
        `${apiBaseUrl}/api/payments/requests/${requestId}/refund`, { ...refund, comment }, { headers: headers() },
    ), 'Возврат отмечен');

    const removeAttachment = (attachment) => run(() => axios.delete(
        `${apiBaseUrl}/api/payments/requests/${requestId}/attachments/${attachment.id}`, { headers: headers() },
    ), 'Файл снят');

    const addFiles = () => run(() => {
        const form = new FormData();
        form.append('step_no', String(current?.step_no || ''));
        appendPayloadFiles(form, files);
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

    const renderStepForm = () => {
        if (!current || !permissions.can_act) return null;
        const no = current.step_no;
        const missingFiles = current.files_required && !(attachmentsByStep[no] || []).length && !files.length;
        return (
            <div className="mt-3 space-y-3 rounded-2xl bg-white p-3.5 ring-1 ring-blue-100">
                {permissions.acting_as_admin && (
                    <div className="text-[12px] text-slate-500">
                        Вы отписываетесь за ответственного ({current.assignee_name || current.role_label}) как администратор раздела — это будет видно в истории.
                    </div>
                )}
                {request.block_code && (
                    <NoticeBox text={request.block_reason} />
                )}
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
                {(current.files?.length > 0 || no === 1) && (
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

    const footer = request ? (
        <>
            {permissions.can_delete && (
                <button type="button" className={`${iosBtnGhost} mr-auto text-rose-600 hover:bg-rose-50`} disabled={busy} onClick={() => setMode(mode === 'delete' ? null : 'delete')}>
                    <Trash2 size={14} /> Удалить
                </button>
            )}
            {permissions.can_refund && (
                <button type="button" className={iosBtnGhost} disabled={busy} onClick={() => { setRefund({ refund_on: request.refund_on ? String(request.refund_on).slice(0, 10) : '', refund_amount: request.refund_amount || 0 }); setMode(mode === 'refund' ? null : 'refund'); }}>
                    Возврат
                </button>
            )}
            {permissions.can_cancel && (
                <button type="button" className={iosBtnGhost} disabled={busy} onClick={() => setMode(mode === 'cancel' ? null : 'cancel')}>Отменить заявку</button>
            )}
            {permissions.can_edit && (
                <button type="button" className={iosBtnSecondary} disabled={busy} onClick={() => onEdit?.(request, data.items)}>
                    <Pencil size={14} /> Изменить
                </button>
            )}
            <button type="button" className={iosBtnPrimary} onClick={onClose}>Готово</button>
        </>
    ) : null;

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
                                    {request.department_name ? ` · ${request.department_name}` : ''}
                                </div>
                            </div>
                            <div className="text-right">
                                <div className="text-[20px] font-semibold tabular-nums text-slate-900">{fmtMoney(request.amount)}</div>
                                {request.refund_amount > 0 && (
                                    <div className="text-[12px] tabular-nums text-slate-500">возврат {fmtMoney(request.refund_amount)} · итого {fmtMoney(request.amount - request.refund_amount)}</div>
                                )}
                            </div>
                        </div>
                        <div className="mt-3 flex flex-wrap items-center gap-2 text-[12.5px]">
                            <StatePill request={request} />
                            {request.status === 'active' && (
                                <span className={`inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 font-medium ${tonePill('current').fill}`}>
                                    Шаг {request.current_step} из 12 · {request.current_assignee_name || ROLE_LABELS[request.current_role] || '—'}
                                </span>
                            )}
                            <span className="rounded-full bg-slate-100 px-2 py-0.5 text-slate-600">{SOURCE_META[request.payment_source]?.label || '—'}</span>
                            <span className="rounded-full bg-slate-100 px-2 py-0.5 text-slate-600">{TYPE_META[request.payment_type]?.label || '—'}</span>
                            {request.due_on && (
                                <span className={`rounded-full px-2 py-0.5 ${state === 'overdue' ? tonePill('overdue').fill : 'bg-slate-100 text-slate-600'}`}>
                                    Срок {fmtDate(request.due_on)} · {dueLabel(request)}
                                </span>
                            )}
                        </div>
                        {request.status === 'rejected' && request.rejected_reason && (
                            <div className="mt-3 text-[13px] text-slate-700"><span className="text-slate-500">Причина отклонения:</span> {request.rejected_reason}</div>
                        )}
                    </div>

                    <ErrorBox text={data && error} />

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

                    {/* Маршрут */}
                    <section className="space-y-1.5">
                        <SectionTitle>Маршрут согласования</SectionTitle>
                        <div className={`${iosCard} p-3.5`}>
                            <ol className="space-y-0">
                                {steps.map((step, index) => {
                                    const isCurrent = step.state === 'current';
                                    const basis = step.step_no === 9 ? request.route_basis : null;
                                    const filesHere = attachmentsByStep[step.step_no] || [];
                                    return (
                                        <li key={step.step_no} className="group relative flex gap-3">
                                            <div className="flex flex-col items-center">
                                                <StepDot state={request.status === 'active' ? step.state : (step.state === 'done' ? 'done' : (step.state === 'skipped' ? 'skipped' : 'pending'))} no={step.step_no} />
                                                {index < steps.length - 1 && <div className={`w-px flex-1 ${step.state === 'done' ? 'bg-emerald-200' : 'bg-slate-200'}`} />}
                                            </div>
                                            <div className={`min-w-0 flex-1 pb-3 ${isCurrent ? '' : ''}`}>
                                                <div className="flex flex-wrap items-baseline justify-between gap-x-3">
                                                    <div className={`text-[13.5px] ${isCurrent ? 'font-semibold text-slate-900' : (step.state === 'pending' || step.state === 'skipped' ? 'text-slate-500' : 'text-slate-800')}`}>
                                                        {step.title}
                                                        {basis && <span className="ml-1.5 inline-flex align-middle"><RouteHint basis={basis} /></span>}
                                                    </div>
                                                    <div className="text-[12px] tabular-nums text-slate-500">
                                                        {step.state === 'done' && step.done_at ? `${fmtDateTime(step.done_at)} · ${step.done_by_name || ''}` : ''}
                                                    </div>
                                                </div>
                                                <div className="text-[12.5px] text-slate-500">
                                                    {/* У пройденного шага имя уже стоит рядом с датой — второй раз
                                                        его не печатаем; остаётся только роль, если отписался её участник. */}
                                                    {step.state === 'skipped'
                                                        ? (step.comment || 'Шаг пропущен')
                                                        : (step.state === 'done' && step.done_by_name && step.done_by_name === step.assignee_name
                                                            ? null
                                                            : (step.assignee_name || step.role_label))}
                                                    {step.state !== 'skipped' && step.assignee_id && step.role_code !== 'initiator' && step.role_code !== 'manager' && (
                                                        <span className="text-slate-400"> · вместо роли «{step.role_label}»</span>
                                                    )}
                                                    {/* «Сменить» — не на каждом шаге постоянно (это шум), а по
                                                        наведению на строку; у текущего шага видно всегда. */}
                                                    {permissions.can_reassign && step.state !== 'done' && (
                                                        <button type="button" className={`ml-2 inline-flex items-center gap-1 text-[12px] text-blue-600 transition hover:underline ${isCurrent || mode === `reassign-${step.step_no}` ? '' : 'opacity-0 group-hover:opacity-100 focus:opacity-100'}`} onClick={() => { setMode(mode === `reassign-${step.step_no}` ? null : `reassign-${step.step_no}`); setReassignTo(step.assignee_id ?? null); }}>
                                                            <UserRoundCog size={12} /> сменить
                                                        </button>
                                                    )}
                                                </div>
                                                {isCurrent && step.brief && <div className="mt-1 text-[12.5px] text-slate-600">{step.brief}</div>}
                                                {step.state === 'done' && step.comment && (
                                                    <div className="mt-1 rounded-lg bg-slate-50 px-2.5 py-1.5 text-[12.5px] text-slate-700">{step.comment}</div>
                                                )}
                                                {!isCurrent && filesHere.length > 0 && (
                                                    <div className="mt-1">
                                                        <AttachmentList attachments={filesHere} apiBaseUrl={apiBaseUrl} headers={headers} canRemove={canRemoveAttachment} onRemove={removeAttachment} showToast={showToast} />
                                                    </div>
                                                )}
                                                {mode === `reassign-${step.step_no}` && (
                                                    <div className="mt-2 space-y-2 rounded-xl bg-slate-50 p-3">
                                                        <UserSelect users={users} value={reassignTo} onChange={setReassignTo} placeholder="Кому передать шаг" />
                                                        <input className={`${iosInput} py-2 text-[13px]`} value={comment} onChange={(event) => setComment(event.target.value)} placeholder="Причина (необязательно)" maxLength={4000} />
                                                        <div className="flex flex-wrap gap-2">
                                                            <button type="button" className={iosBtnPrimary} disabled={busy} onClick={() => reassign(step.step_no)}>Передать</button>
                                                            {step.role_code !== 'initiator' && step.role_code !== 'manager' && (
                                                                <button type="button" className={iosBtnSecondary} disabled={busy} onClick={() => { setReassignTo(null); setTimeout(() => reassign(step.step_no), 0); }}>Вернуть роли «{step.role_label}»</button>
                                                            )}
                                                            <button type="button" className={iosBtnGhost} onClick={() => setMode(null)}>Отмена</button>
                                                        </div>
                                                    </div>
                                                )}
                                                {isCurrent && (
                                                    permissions.can_act
                                                        ? renderStepForm()
                                                        : (
                                                            <div className="mt-2 text-[12.5px] text-slate-500">
                                                                Ждём отписки: {step.assignee_name || step.role_label}.
                                                                {request.block_code && <div className="mt-1 text-amber-700">{request.block_reason}</div>}
                                                            </div>
                                                        )
                                                )}
                                            </div>
                                        </li>
                                    );
                                })}
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
                            <Row label="Юр. лицо">{request.legal_entity_name}</Row>
                            <Row label="Филиал / регион">{request.branch}</Row>
                            <Row label="Период оплаты">{request.payment_period}</Row>
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
                                <Row label="Согласующий счёта">{request.route_basis ? `${routeSummary(request.route_basis).approver}${request.route_basis.order_number ? ` · по Приказу №${request.route_basis.order_number}` : ''}` : null}</Row>
                                <Row label="Оплачено">{request.paid_on ? `${fmtDate(request.paid_on)} · ${fmtMoney(request.paid_amount)}` : null}</Row>
                                <Row label="Возврат">{request.refund_amount > 0 ? `${fmtDate(request.refund_on)} · ${fmtMoney(request.refund_amount)}` : null}</Row>
                            </div>
                        </section>
                    )}

                    {(data.attachments || []).length > 0 && (
                        <section className="space-y-1.5">
                            <SectionTitle>Файлы</SectionTitle>
                            <div className={`${iosCard} p-2`}>
                                <AttachmentList attachments={data.attachments} apiBaseUrl={apiBaseUrl} headers={headers} canRemove={canRemoveAttachment} onRemove={removeAttachment} showToast={showToast} showStep />
                                {(permissions.can_edit || permissions.can_act) && !isClosed(request) && (
                                    <div className="px-2 pt-1">
                                        <FilePicker files={files} onChange={setFiles} kinds={stepFileKinds} label="Добавить файл" />
                                        {files.length > 0 && !permissions.can_act && (
                                            <button type="button" className={`${iosBtnSecondary} mt-2`} disabled={busy} onClick={addFiles}>Загрузить</button>
                                        )}
                                    </div>
                                )}
                            </div>
                        </section>
                    )}

                    <section className="space-y-1.5">
                        <SectionTitle>История</SectionTitle>
                        <div className={`${iosCard} px-4 py-2`}>
                            {(data.events || []).slice().reverse().map((event) => (
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
