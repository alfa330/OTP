import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import { Ban, Check, ChevronDown, Loader2, Pencil, RotateCcw, Trash2, UserRoundCog, X } from 'lucide-react';
import { iosBtnGhost, iosBtnPrimary, iosBtnSecondary, iosCard, iosInput, IosMenu, IosModal } from '../ui/ios';
import IosDatePicker from '../ui/DatePicker';
import PaymentStepForm from './PaymentStepForm';
import {
    ROLE_LABELS, SOURCE_META, TYPE_META, approverBasisLine, attachmentKindsForStep, cardNumberLabel, describeEvent,
    dueLabel, fmtDate, fmtDateTime, fmtMoney, fmtQty, isClosed, netAmount, passedStepsLabel, requestState, splitRoute,
    supplierLabel, tonePill,
} from './paymentsMeta';
import {
    AmountInput, AttachmentList, ErrorBox, Field, FilePicker, Row, RouteHint, SectionTitle, StatePill, UserSelect,
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
 * частое действие над заявкой. Форма шага — PaymentStepForm.jsx: у каждого шага
 * своя, с выбором вариантов вместо текста. Пользователю без права на шаг форма
 * не рисуется вовсе — кнопка, которая всегда отвечает отказом, хуже её
 * отсутствия.
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

const PaymentRequestCard = ({
    open, onClose, requestId, apiBaseUrl, headers, dictionaries, users, me, onChanged, onEdit, showToast,
}) => {
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');
    const [busy, setBusy] = useState(false);
    const [comment, setComment] = useState('');
    const [extraFiles, setExtraFiles] = useState([]);
    const [mode, setMode] = useState(null);       // 'return' | 'reject' | 'cancel' | 'refund' | 'delete' | 'reassign-<n>'
    const [reassignTo, setReassignTo] = useState(null);
    const [refund, setRefund] = useState({ refund_on: '', refund_amount: 0 });
    const [showFolded, setShowFolded] = useState(false);
    const [showAllEvents, setShowAllEvents] = useState(false);
    const modePanelRef = useRef(null);
    const errorRef = useRef(null);

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
        setExtraFiles([]);
        setMode(null);
        setShowFolded(false);
        setShowAllEvents(false);
        load();
    }, [open, load]);

    /* Отказ сервера показан над маршрутом, а человек в этот момент у кнопки шага
       ниже — подкручиваем к тексту ошибки, иначе нажатие выглядит как «ничего
       не произошло». */
    useEffect(() => {
        if (error && data) errorRef.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    }, [error, data]);

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

    const complete = ({ fields: stepFields, comment: stepComment, files: stepFiles }) => run(() => {
        const form = new FormData();
        form.append('payload', JSON.stringify({ ...stepFields, comment: stepComment }));
        appendPayloadFiles(form, stepFiles);
        return axios.post(`${apiBaseUrl}/api/payments/requests/${requestId}/steps/${current.step_no}/complete`, form, { headers: headers() });
    }, 'Шаг отписан');

    const returnStep = (reason) => run(() => axios.post(
        `${apiBaseUrl}/api/payments/requests/${requestId}/steps/${current.step_no}/return`, { comment: reason }, { headers: headers() },
    ), 'Заявка возвращена на доработку');

    const rejectStep = (reason) => run(() => axios.post(
        `${apiBaseUrl}/api/payments/requests/${requestId}/steps/${current.step_no}/reject`, { comment: reason }, { headers: headers() },
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

    const attachmentsByStep = useMemo(() => {
        const map = {};
        (data?.attachments || []).forEach((item) => { (map[item.step_no || 0] ||= []).push(item); });
        return map;
    }, [data]);

    const canRemoveAttachment = (attachment) => permissions.can_delete
        || (attachment.uploaded_by === me?.id && current && attachment.step_no === current.step_no);

    const stepFileKinds = useMemo(() => attachmentKindsForStep(current), [current]);
    const stepTakesFiles = Boolean(current && (current.files?.length > 0 || current.step_no === 1));

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
                            {/* Что сделать на шаге — простыми словами, для того, кто здесь впервые. */}
                            {step.brief && <div className="mt-0.5 text-slate-500">{step.brief}</div>}
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
                            ? (
                                <PaymentStepForm
                                    key={`${request.id}:${step.step_no}`}
                                    request={request}
                                    step={step}
                                    steps={steps}
                                    data={data}
                                    permissions={permissions}
                                    dictionaries={dictionaries}
                                    apiBaseUrl={apiBaseUrl}
                                    headers={headers}
                                    busy={busy}
                                    showToast={showToast}
                                    canRemoveAttachment={canRemoveAttachment}
                                    onRemoveAttachment={removeAttachment}
                                    onComplete={complete}
                                    onReturn={returnStep}
                                    onReject={rejectStep}
                                    onEdit={() => onEdit?.(request, data.items)}
                                />
                            )
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

                    <div ref={errorRef} className="scroll-mt-4"><ErrorBox text={data && error} /></div>

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
                            <Row label="Контрагент">{[request.counterparty_name, request.counterparty_bin && `БИН ${request.counterparty_bin}`, supplierLabel(request)].filter(Boolean).join(' · ')}</Row>
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
