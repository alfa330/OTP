import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import { Ban, Check, Circle, ExternalLink, Loader2, Minus, Pencil, RotateCcw, Trash2, UserRoundCog } from 'lucide-react';
import { iosBtnGhost, iosBtnPrimary, iosBtnSecondary, iosCard, iosInput, IosMenu, IosModal } from '../ui/ios';
import IosDatePicker from '../ui/DatePicker';
import PaymentStageForm from './PaymentStageForm';
import {
    ACCOUNTING_CATEGORY_LABELS, ASSET_STATUS_META, CARD_RECIPIENT_LABELS, DOCS_STATUS_META, OBJECT_TYPE_LABELS,
    PAYMENT_METHOD_LABELS, REQUEST_KIND_LABELS, approvalSummary, describeEvent, dueLabel, fileKindOptions, fmtDate,
    fmtDateTime, fmtMoney, fmtQty, isClosed, isOverdue, netAmount, paidLabel, requestState, tonePill,
} from './paymentsMeta';
import {
    AmountInput, ApprovalHint, AttachmentList, CardNumber, DATE_TRIGGER, ErrorBox, Field, FilePicker, Lifecycle,
    NoticeBox, RequisitesBox, Row, SectionTitle, StatePill, TonePill, UserSelect, errorText, multipart,
} from './paymentsUi';

/*
 * Карточка заявки «Закуп товара/услуги» (ТЗ «Закуп и оплата», п. 2, п. 20).
 *
 * Заявка одна, а этапы — внутри неё: полоса этапов наверху показывает, где
 * заявка сейчас. Человек открывает карточку, чтобы сделать своё дело, поэтому
 * первой идёт ТЕКУЩАЯ ЗАДАЧА — с формой действия, если она его; остальным
 * карточка говорит, у кого заявка и чего ждёт. Формы действий — в
 * PaymentStageForm.jsx. Пользователю без права на действие форма не рисуется
 * вовсе: кнопка, которая всегда отвечает отказом, хуже её отсутствия.
 *
 * Ниже — сама заявка по разделам: что закупаем, поставщики, оплата,
 * согласование, получение и документы, ход по этапам, файлы, история.
 * Файлы живут в одном месте — «Документы», с подписью этапа.
 *
 * Редкие действия (возврат средств, отмена, удаление) — в меню «…» подвала:
 * на виду только «Доработать» и «Готово».
 */

const EVENTS_SHOWN = 6;

const shortDateTime = (value) => {
    const text = fmtDateTime(value);
    return text.length > 10 ? `${text.slice(0, 5)} ${text.slice(11)}` : text;
};

const StateDot = ({ state }) => {
    if (state === 'done') {
        return <span className="grid h-5 w-5 shrink-0 place-items-center rounded-full bg-emerald-500 text-white"><Check size={12} strokeWidth={3} /></span>;
    }
    if (state === 'open') {
        return <span className="grid h-5 w-5 shrink-0 place-items-center rounded-full bg-blue-600 text-white ring-4 ring-blue-100"><Circle size={7} fill="currentColor" /></span>;
    }
    if (state === 'waiting') {
        return <span className="grid h-5 w-5 shrink-0 place-items-center rounded-full bg-amber-400 text-white"><Minus size={12} strokeWidth={3} /></span>;
    }
    if (state === 'skipped') {
        return <span className="grid h-5 w-5 shrink-0 place-items-center rounded-full bg-slate-100 text-[11px] text-slate-400">—</span>;
    }
    return <span className="h-5 w-5 shrink-0 rounded-full bg-white ring-1 ring-slate-200" />;
};

const PaymentRequestCard = ({
    open, onClose, requestId, apiBaseUrl, headers, dictionaries, users, me, meta, onChanged, onEdit, showToast,
}) => {
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');
    const [busy, setBusy] = useState(false);
    const [comment, setComment] = useState('');
    const [extraFiles, setExtraFiles] = useState([]);
    const [mode, setMode] = useState(null);       // 'cancel' | 'refund' | 'delete' | 'reassign:<kind>'
    const [reassignTo, setReassignTo] = useState(null);
    const [refund, setRefund] = useState({ refund_on: '', refund_amount: 0 });
    const [showAllEvents, setShowAllEvents] = useState(false);
    const modePanelRef = useRef(null);
    const errorRef = useRef(null);

    const toastRef = useRef(showToast);
    useEffect(() => { toastRef.current = showToast; }, [showToast]);

    const base = `${apiBaseUrl}/api/payments/requests/${requestId}`;

    // `keepError` — перечитать заявку, не стирая отказ: после «этап уже выполнил
    // другой» человек должен увидеть и причину, и заявку как она есть сейчас.
    // Билет отсекает ответ по прежней заявке, если за это время открыли другую.
    const ticketRef = useRef(0);
    const load = useCallback(async ({ keepError = false } = {}) => {
        if (!requestId) return;
        const ticket = ticketRef.current + 1;
        ticketRef.current = ticket;
        setLoading(true);
        if (!keepError) setError('');
        try {
            const response = await axios.get(`${apiBaseUrl}/api/payments/requests/${requestId}`, { headers: headers() });
            if (ticketRef.current === ticket) setData(response.data);
        } catch (err) {
            if (ticketRef.current === ticket) setError(errorText(err, 'Не удалось загрузить заявку'));
        } finally {
            if (ticketRef.current === ticket) setLoading(false);
        }
    }, [apiBaseUrl, headers, requestId]);

    useEffect(() => {
        if (!open) { setData(null); setMode(null); return; }
        // Открыли другую заявку поверх прежней (щелчок по колоколу) — прежнюю не показываем.
        setData((prev) => (prev?.request?.id === requestId ? prev : null));
        setExtraFiles([]);
        setMode(null);
        setShowAllEvents(false);
        load();
    }, [open, load, requestId]);

    // У каждой панели (отмена, возврат, смена исполнителя) свой текст: набранное
    // в одной не должно всплывать причиной в другой.
    useEffect(() => { setComment(''); }, [mode, open]);

    /* Отказ сервера показан над задачей, а человек в этот момент у кнопки
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
    const subtasks = useMemo(() => data?.subtasks || [], [data]);
    const permissions = data?.permissions || {};
    const active = request?.status === 'active';
    const current = useMemo(() => (active ? subtasks.find((item) => item.state === 'open') : null), [subtasks, active]);
    const state = requestState(request);
    const titleOf = useCallback((kind) => (meta?.subtasks || []).find((item) => item.kind === kind)?.title || '', [meta]);

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
            // Заявку успел изменить кто-то другой — показываем её как есть сейчас, вместе с отказом.
            if (err?.response?.status === 409) load({ keepError: true });
            return false;
        } finally {
            setBusy(false);
        }
    }, [apply, onChanged, load]);

    const DONE_TEXT = {
        approve: 'Согласовано', return: 'Заявка возвращена на доработку', reject: 'Заявка отклонена',
        request_info: 'Запрос отправлен инициатору', pay: 'Оплата записана', top_up: 'Пополнение записано',
        provide: 'Чек отправлен', confirm: 'Получение подтверждено', register: 'Имущество поставлено на учёт',
        docs_original: 'Отмечено: оригинал получен', docs_close: 'Документы закрыты',
    };

    const act = ({ action, fields, files }) => run(() => axios.post(
        `${base}/act`, multipart({ kind: current.kind, action, ...fields }, files), { headers: headers() },
    ), DONE_TEXT[action]);

    const move = (status) => run(() => axios.post(
        `${base}/move`, { kind: current.kind, status }, { headers: headers() },
    ));

    const reply = ({ comment: text, files }) => run(() => axios.post(
        `${base}/submit`, multipart({ comment: text }, files), { headers: headers() },
    ), 'Заявка отправлена');

    const attach = (files) => run(() => axios.post(
        `${base}/attachments`, multipart({}, files), { headers: headers() },
    ), 'Файлы добавлены');

    const cancel = () => run(() => axios.post(`${base}/cancel`, { comment }, { headers: headers() }), 'Заявка отменена');

    const reassign = (kind, userId) => run(() => axios.post(
        `${base}/assignee`, { kind, user_id: userId, reason: comment }, { headers: headers() },
    ), 'Исполнитель изменён');

    const saveRefund = () => run(() => axios.post(
        `${base}/refund`, { ...refund, comment }, { headers: headers() },
    ), refund.refund_amount > 0 ? 'Возврат отмечен' : 'Возврат снят');

    const removeAttachment = (attachment) => run(() => axios.delete(
        `${base}/attachments/${attachment.id}`, { headers: headers() },
    ), 'Файл снят');

    const remove = async () => {
        const ok = await run(() => axios.delete(base, { headers: headers() }), 'Заявка удалена');
        if (ok) { onChanged?.({ id: requestId, deleted: true }); onClose?.(); }
    };

    const loadCard = useCallback(async () => {
        const response = await axios.get(`${apiBaseUrl}/api/payments/requests/${requestId}/card`, { headers: headers() });
        return response.data;
    }, [apiBaseUrl, headers, requestId]);

    const subtaskByKind = useMemo(() => Object.fromEntries(subtasks.map((item) => [item.kind, item])), [subtasks]);
    const canRemoveAttachment = (attachment) => {
        if (permissions.can_delete) return true;
        const stage = attachment.subtask_kind ? subtaskByKind[attachment.subtask_kind] : null;
        const stageOpen = !attachment.subtask_kind || ['open', 'waiting'].includes(stage?.state);
        return attachment.uploaded_by === me?.id && active && stageOpen;
    };

    const offerFiles = useMemo(() => {
        const map = {};
        (data?.attachments || []).forEach((item) => { if (item.offer_id) (map[item.offer_id] ||= []).push(item); });
        return map;
    }, [data]);

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
                <button type="button" className={iosBtnSecondary} disabled={busy} onClick={() => onEdit?.(data)}>
                    <Pencil size={14} /> Доработать
                </button>
            )}
            <button type="button" className={iosBtnPrimary} onClick={onClose}>Готово</button>
        </>
    ) : null;

    const events = (data?.events || []).slice().reverse();
    const shownEvents = showAllEvents ? events : events.slice(0, EVENTS_SHOWN);
    const approval = approvalSummary(request?.route_basis);
    const docsMeta = DOCS_STATUS_META[request?.closing_docs_status] || DOCS_STATUS_META.none;
    const closingStarted = Boolean(request && (request.received_on || subtaskByKind.closing_docs?.state === 'open'
        || subtaskByKind.closing_docs?.state === 'done'));
    const waiting = subtasks.find((item) => item.state === 'waiting') || null;
    const overdue = isOverdue(request);

    /* Строка этапа в «Ходе заявки». */
    const renderSubtask = (item, isLast) => {
        const reassignOpen = mode === `reassign:${item.kind}`;
        const who = item.assignee_name || item.role_label;
        let right = '';
        if (item.state === 'done' && item.done_at) right = `${item.done_by_name || ''} · ${shortDateTime(item.done_at)}`;
        else if (item.state !== 'skipped') right = who;
        // Этапы бухгалтерии и финансового отдела выполняет подразделение целиком
        // (пп. 2 и 9 ТЗ) — сотрудника им не назначают.
        const canReassign = permissions.can_reassign && item.kind !== 'initiation'
            && !['accounting', 'finance'].includes(item.role_code) && ['pending', 'open', 'waiting'].includes(item.state);
        return (
            <li key={item.kind} className="group relative flex gap-3">
                <div className="flex w-5 shrink-0 flex-col items-center pt-px">
                    <StateDot state={item.state} />
                    {!isLast && <div className={`mt-1 w-px flex-1 ${item.state === 'done' ? 'bg-emerald-200' : 'bg-slate-200'}`} />}
                </div>
                <div className={`min-w-0 flex-1 ${isLast ? '' : 'pb-3'}`}>
                    <div className="flex items-baseline justify-between gap-3">
                        <div className={`min-w-0 text-[13.5px] ${item.state === 'open' ? 'font-semibold text-slate-900' : (item.state === 'done' ? 'text-slate-800' : 'text-slate-500')}`}>
                            {item.title}
                        </div>
                        <div className="flex max-w-[58%] shrink-0 items-baseline gap-1.5">
                            {canReassign && (
                                <button
                                    type="button"
                                    aria-label="Сменить исполнителя"
                                    title="Сменить исполнителя"
                                    // На телефоне наведения нет — значок виден всегда; на компьютере проявляется у строки под курсором.
                                    className={`self-center rounded-full p-0.5 text-blue-600 transition hover:bg-blue-50 ${reassignOpen ? '' : 'md:opacity-0 md:group-hover:opacity-100 md:focus:opacity-100'}`}
                                    onClick={() => { setMode(reassignOpen ? null : `reassign:${item.kind}`); setReassignTo(item.assignee_id ?? null); }}
                                >
                                    <UserRoundCog size={13} />
                                </button>
                            )}
                            {right && <div className="truncate text-right text-[12px] tabular-nums text-slate-500">{right}</div>}
                        </div>
                    </div>
                    {item.state === 'skipped' && <div className="text-[12.5px] text-slate-400">{item.comment || 'Этап не требуется'}</div>}
                    {item.state === 'waiting' && (
                        <div className="text-[12.5px] text-amber-700">
                            Ждёт инициатора: {item.clarify_label || 'требуется уточнение'}{item.clarify_comment ? ` — ${item.clarify_comment}` : ''}
                        </div>
                    )}
                    {item.state === 'done' && item.comment && item.kind !== 'initiation' && (
                        <div className="whitespace-pre-line text-[12.5px] text-slate-600">{item.comment}</div>
                    )}
                    {reassignOpen && (
                        <div className="mt-2 space-y-2 rounded-xl bg-slate-50 p-3 motion-safe:animate-reveal">
                            <UserSelect compact users={users} value={reassignTo} onChange={setReassignTo} placeholder="Кому передать этап" />
                            <input className={`${iosInput} bg-white !py-2 !text-[13px]`} value={comment} onChange={(event) => setComment(event.target.value)} placeholder="Причина (необязательно)" maxLength={4000} />
                            <div className="flex flex-wrap gap-2">
                                <button type="button" className={iosBtnPrimary} disabled={busy || !reassignTo} onClick={() => reassign(item.kind, reassignTo)}>Передать</button>
                                {item.role_code !== 'manager' && item.assignee_id && (
                                    <button type="button" className={iosBtnSecondary} disabled={busy} onClick={() => reassign(item.kind, null)}>Вернуть: «{item.role_label}»</button>
                                )}
                                <button type="button" className={iosBtnGhost} onClick={() => setMode(null)}>Отмена</button>
                            </div>
                        </div>
                    )}
                </div>
            </li>
        );
    };

    return (
        <IosModal
            open={open}
            onClose={onClose}
            title={request ? `Заявка №${request.id}` : 'Заявка'}
            subtitle={request ? `${REQUEST_KIND_LABELS[request.request_kind] || 'Закуп'} · ${fmtDateTime(request.created_at)} · ${request.initiator_name || ''}` : ''}
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
                    {/* Сводка: что, сколько, на каком этапе. Цвет только у состояний со смыслом. */}
                    <div className={`${iosCard} p-4`}>
                        {/* grow basis-0, а не flex-1: по нему оболочка телефона ставит ряд столбиком. */}
                        <div className="flex flex-wrap items-start justify-between gap-3">
                            <div className="min-w-0 grow basis-0">
                                <div className="text-[16px] font-semibold leading-snug text-slate-900">{request.expense_name}</div>
                                <div className="mt-0.5 text-[13px] text-slate-500">
                                    {[request.counterparty_name || 'Поставщик не выбран', request.legal_entity_name].filter(Boolean).join(' · ')}
                                </div>
                            </div>
                            <div className="text-right">
                                <div className="text-[20px] font-semibold tabular-nums text-slate-900">{fmtMoney(netAmount(request))}</div>
                                {request.refund_amount > 0 && (
                                    <div className="text-[12px] tabular-nums text-slate-500">{fmtMoney(request.amount)} − возврат {fmtMoney(request.refund_amount)}</div>
                                )}
                            </div>
                        </div>
                        <div className="mt-3">
                            <Lifecycle stages={data.lifecycle} stopped={!active && request.status !== 'done'} />
                        </div>
                        {(state !== 'active' || request.due_on) && (
                            <div className="mt-2.5 flex flex-wrap items-center gap-2 text-[12.5px]">
                                <StatePill request={request} />
                                {request.due_on && !isClosed(request) && (
                                    <span className={`rounded-full px-2 py-0.5 ${overdue ? tonePill('overdue').fill : 'bg-slate-100 text-slate-600'}`}>
                                        {/* У оплаченной «к 19.10» повторяло бы дату слева — пишем, когда оплатили. */}
                                        Срок {fmtDate(request.due_on)} · {request.paid_on ? paidLabel(request) : dueLabel(request)}
                                    </span>
                                )}
                            </div>
                        )}
                        {['rejected', 'cancelled'].includes(request.status) && request.rejected_reason && (
                            <div className="mt-3 text-[13px] text-slate-700">
                                <span className="text-slate-500">{request.status === 'rejected' ? 'Причина отклонения:' : 'Почему отменена:'}</span> {request.rejected_reason}
                            </div>
                        )}
                    </div>

                    <div ref={errorRef} className="scroll-mt-4"><ErrorBox text={data && error} /></div>

                    <div ref={modePanelRef}>
                        {mode === 'cancel' && (
                            <div className="space-y-2 rounded-2xl bg-slate-50 p-3.5 ring-1 ring-slate-200 motion-safe:animate-reveal">
                                <div className="text-[13px] text-slate-700">Заявка будет закрыта как отменённая. Оплаты по ней не было.</div>
                                <textarea className={`${iosInput} min-h-[56px] resize-y bg-white`} value={comment} onChange={(event) => setComment(event.target.value)} placeholder="Почему отменяем (необязательно)" maxLength={4000} />
                                <div className="flex gap-2">
                                    <button type="button" className={iosBtnPrimary} disabled={busy} onClick={cancel}>Отменить заявку</button>
                                    <button type="button" className={iosBtnSecondary} onClick={() => setMode(null)}>Назад</button>
                                </div>
                            </div>
                        )}
                        {mode === 'delete' && (
                            <div className="space-y-2 rounded-2xl bg-rose-50 p-3.5 ring-1 ring-rose-200 motion-safe:animate-reveal">
                                <div className="text-[13px] text-rose-800">Удалить заявку вместе с историей и файлами? Это не отменить. Обычно достаточно «Отменить заявку» — она останется в реестре.</div>
                                <div className="flex gap-2">
                                    <button type="button" className={`${iosBtnPrimary} !bg-rose-600 hover:!bg-rose-700`} disabled={busy} onClick={remove}>Удалить навсегда</button>
                                    <button type="button" className={iosBtnSecondary} onClick={() => setMode(null)}>Назад</button>
                                </div>
                            </div>
                        )}
                        {mode === 'refund' && (
                            <div className="space-y-3 rounded-2xl bg-slate-50 p-3.5 ring-1 ring-slate-200 motion-safe:animate-reveal">
                                <div className="text-[13px] text-slate-700">Возврат средств: итоговая сумма расхода пересчитается как сумма − возврат.</div>
                                <div className="grid gap-3 sm:grid-cols-2">
                                    <Field label="Дата возврата" required optionalMark={false}>
                                        <IosDatePicker value={refund.refund_on} onChange={(value) => setRefund((prev) => ({ ...prev, refund_on: value || '' }))} allowEmpty placeholder="Дата" triggerClassName={`${DATE_TRIGGER} !bg-white`} ariaLabel="Дата возврата" />
                                    </Field>
                                    <Field label="Сумма возврата" required optionalMark={false} hint="Полная или частичная. Ноль — снять возврат.">
                                        <AmountInput className="bg-white" value={refund.refund_amount} onChange={(value) => setRefund((prev) => ({ ...prev, refund_amount: value }))} ariaLabel="Сумма возврата" />
                                    </Field>
                                </div>
                                <div className="flex gap-2">
                                    <button type="button" className={iosBtnPrimary} disabled={busy || (refund.refund_amount > 0 && !refund.refund_on)} onClick={saveRefund}>Сохранить</button>
                                    <button type="button" className={iosBtnSecondary} onClick={() => setMode(null)}>Назад</button>
                                </div>
                            </div>
                        )}
                    </div>

                    {/* Текущая задача: форма — тому, чья она; остальным — у кого заявка. */}
                    {current && (
                        <section className="space-y-1.5">
                            <SectionTitle>{permissions.can_act ? 'Ваша задача' : 'Сейчас'} · {current.title}</SectionTitle>
                            {permissions.can_act ? (
                                <PaymentStageForm
                                    key={`${request.id}:${current.kind}`}
                                    data={data}
                                    subtask={current}
                                    dictionaries={dictionaries}
                                    users={users}
                                    meta={meta}
                                    apiBaseUrl={apiBaseUrl}
                                    headers={headers}
                                    busy={busy}
                                    showToast={showToast}
                                    permissions={permissions}
                                    onAct={act}
                                    onMove={move}
                                    onEdit={() => onEdit?.(data)}
                                    onReply={reply}
                                    onAttach={attach}
                                    loadCard={loadCard}
                                />
                            ) : (
                                <div className={`${iosCard} px-4 py-3 text-[13px] text-slate-700`}>
                                    Заявка у: <span className="font-medium text-slate-900">{current.assignee_name || current.role_label}</span>
                                    {waiting && current.kind === 'initiation' && (
                                        <div className="mt-1 text-amber-700">
                                            {waiting.clarify_label || 'Требуется уточнение'}{waiting.clarify_comment ? ` — ${waiting.clarify_comment}` : ''}
                                        </div>
                                    )}
                                </div>
                            )}
                        </section>
                    )}

                    {/* Чужая задача — а закрывающие документы от инициатора всё равно нужны. */}
                    {active && !permissions.can_act && request.initiator_id === me?.id && current?.kind === 'closing_docs'
                        && request.closing_docs_status === 'none' && (
                        <NoticeBox text="Бухгалтерия ждёт закрывающие документы: приложите накладную, акт или чек в разделе «Документы» ниже. Оригиналы передайте в бухгалтерию." />
                    )}

                    {(data.duplicates || []).length > 0 && current?.kind !== 'invoice_payment' && !request.paid_on && active && (
                        <NoticeBox text="">
                            <span className="font-medium">Возможный дубль счёта.</span> Тот же поставщик, номер, сумма и дата:
                            {' '}{data.duplicates.map((item) => `заявка №${item.id}`).join(', ')}.
                        </NoticeBox>
                    )}

                    <section className="space-y-1.5">
                        <SectionTitle>Заявка</SectionTitle>
                        <div className={`${iosCard} px-4 py-2`}>
                            <Row label="Описание и обоснование" wide>{request.justification}</Row>
                            <Row label="Позиции" wide>
                                <div className="space-y-0.5">
                                    {(data.items || []).map((item) => (
                                        <div key={item.id} className="flex flex-wrap items-baseline justify-between gap-x-3">
                                            <span>{item.name}</span>
                                            <span className="tabular-nums text-slate-600">{fmtQty(item.quantity)}{item.unit ? ` ${item.unit}` : ''} × {fmtMoney(item.unit_price)} = {fmtMoney(item.total)}</span>
                                        </div>
                                    ))}
                                </div>
                            </Row>
                            <Row label="Тип объекта" wide>{[OBJECT_TYPE_LABELS[request.object_type], ACCOUNTING_CATEGORY_LABELS[request.accounting_category]].filter(Boolean).join(' · ')}</Row>
                            <Row label="Категория закупа" wide>{[request.category_name, request.subcategory_name].filter(Boolean).join(' → ')}</Row>
                            <Row label="Компания" wide>{request.legal_entity_name}</Row>
                            <Row label="Подразделение" wide>{request.department_name}</Row>
                            <Row label="Проект" wide>{request.project_name}</Row>
                            <Row label="Филиал / регион" wide>{request.branch}</Row>
                            <Row label="Период оплаты" wide>{request.payment_period}</Row>
                            <Row label="Регулярный платёж" wide>{request.template_name}</Row>
                            <Row label="Комментарий" wide>{request.notes}</Row>
                        </div>
                        {permissions.can_edit && <RequisitesBox entity={data.legal_entity} />}
                    </section>

                    {(data.offers || []).length > 0 && (
                        <section className="space-y-1.5">
                            <SectionTitle>{request.no_alternatives ? 'Поставщик' : 'Поставщики'}</SectionTitle>
                            <div className={`${iosCard} divide-y divide-slate-100`}>
                                {data.offers.map((offer) => (
                                    <div key={offer.id} className="px-4 py-2.5">
                                        <div className="flex flex-wrap items-baseline justify-between gap-x-3">
                                            <span className="text-[13.5px] text-slate-900">
                                                {offer.supplier_name || '—'}
                                                {offer.is_recommended && !request.no_alternatives && <span className="ml-2 rounded-md bg-blue-50 px-1.5 py-[2px] text-[11px] font-semibold text-blue-700">Рекомендуемый</span>}
                                            </span>
                                            <span className="text-[13.5px] tabular-nums text-slate-900">{offer.amount !== null && offer.amount !== undefined ? fmtMoney(offer.amount) : '—'}</span>
                                        </div>
                                        {(offer.terms || offer.comment || offer.link || offerFiles[offer.id]) && (
                                            <div className="mt-0.5 space-y-0.5 text-[12.5px] text-slate-600">
                                                {offer.terms && <div>{offer.terms}</div>}
                                                {offer.comment && <div className="text-slate-500">{offer.comment}</div>}
                                                {offer.link && (
                                                    <a href={offer.link} target="_blank" rel="noopener noreferrer" className="inline-flex max-w-full items-center gap-1 text-blue-600 hover:underline">
                                                        <ExternalLink size={12} className="shrink-0" /><span className="truncate">{offer.link}</span>
                                                    </a>
                                                )}
                                                {offerFiles[offer.id] && (
                                                    <AttachmentList attachments={offerFiles[offer.id]} apiBaseUrl={apiBaseUrl} headers={headers} showToast={showToast} />
                                                )}
                                            </div>
                                        )}
                                    </div>
                                ))}
                                {(request.no_alternatives || request.supplier_choice_reason) && (
                                    <div className="px-4 py-2">
                                        <Row label="Альтернативные предложения" wide>
                                            {request.no_alternatives ? ['отсутствуют', (meta?.no_alternatives_reasons || []).find((item) => item.code === request.no_alternatives_reason)?.label, request.no_alternatives_comment].filter(Boolean).join(' — ') : null}
                                        </Row>
                                        <Row label="Обоснование выбора" wide>{request.supplier_choice_reason}</Row>
                                    </div>
                                )}
                            </div>
                        </section>
                    )}

                    <section className="space-y-1.5">
                        <SectionTitle>Оплата и согласование</SectionTitle>
                        <div className={`${iosCard} px-4 py-2`}>
                            <Row label="Способ оплаты" wide>{PAYMENT_METHOD_LABELS[request.payment_method]}</Row>
                            {request.payment_method === 'card' ? (
                                <>
                                    <Row label="Получатель" wide>{[CARD_RECIPIENT_LABELS[request.card_recipient], request.card_holder_name].filter(Boolean).join(' · ')}</Row>
                                    <Row label="Номер карты" wide>
                                        {request.card_mask ? <CardNumber mask={request.card_mask} canReveal={permissions.can_view_card} load={loadCard} showToast={showToast} /> : null}
                                    </Row>
                                    <Row label="Назначение" wide>{request.payment_purpose}</Row>
                                </>
                            ) : (
                                <>
                                    <Row label="Счёт" wide>{request.invoice_number || request.invoice_date ? `${request.invoice_number ? `№${request.invoice_number}` : ''}${request.invoice_date ? ` от ${fmtDate(request.invoice_date)}` : ''}`.trim() : null}</Row>
                                    <Row label="Договор" wide>{request.contract_number ? `№${request.contract_number}` : null}</Row>
                                    <Row label="Реквизиты поставщика" wide>{data.counterparty_account?.text}</Row>
                                    <Row label="Назначение платежа" wide>{request.payment_purpose}</Row>
                                </>
                            )}
                            <Row label="Руководитель" wide>{request.manager_name}</Row>
                            <Row label="Согласует" wide>
                                {approval ? (
                                    <span className="inline-flex flex-wrap items-center gap-1.5">
                                        {approval.approver}
                                        <ApprovalHint basis={request.route_basis} />
                                        {approval.basisText && <span className="text-slate-500">· {approval.basisText}</span>}
                                    </span>
                                ) : null}
                            </Row>
                            <Row label="Согласовано" wide>
                                {request.approved_at ? `${request.approved_by_name || ''} · ${fmtDateTime(request.approved_at)}${request.amount_approved ? ` · ${fmtMoney(request.amount_approved)}` : ''}` : null}
                            </Row>
                            <Row label="Оплачено" wide>
                                {request.paid_on ? `${fmtDate(request.paid_on)} · ${fmtMoney(request.paid_amount)}${request.paid_comment ? ` · ${request.paid_comment}` : ''}` : null}
                            </Row>
                            <Row label="Возврат" wide>{request.refund_amount > 0 ? `${fmtDate(request.refund_on)} · ${fmtMoney(request.refund_amount)}` : null}</Row>
                        </div>
                    </section>

                    {closingStarted && (
                        <section className="space-y-1.5">
                            <SectionTitle>Получение и документы</SectionTitle>
                            <div className={`${iosCard} px-4 py-2`}>
                                <Row label="Получено" wide>
                                    {request.received_on ? `${fmtDate(request.received_on)}${request.received_quantity ? ` · ${request.received_quantity}` : ''}` : null}
                                </Row>
                                <Row label="Закрывающие документы" wide><TonePill tone={docsMeta.tone}>{docsMeta.label}</TonePill></Row>
                                {(data.assets || []).length > 0 && (
                                    <Row label="Имущество на учёте" wide>
                                        <div className="space-y-1">
                                            {data.assets.map((asset) => (
                                                <div key={asset.id}>
                                                    <span className="tabular-nums">{asset.inventory_number}</span> · {asset.name}
                                                    <div className="text-[12.5px] text-slate-500">
                                                        {[asset.responsible_name, asset.city, asset.location, ASSET_STATUS_META[asset.status]?.label].filter(Boolean).join(' · ')}
                                                    </div>
                                                </div>
                                            ))}
                                        </div>
                                    </Row>
                                )}
                            </div>
                        </section>
                    )}

                    {/* Условия закрытия (п. 15) — пока заявка не закрыта и оплата уже прошла. */}
                    {active && request.paid_on && (
                        <section className="space-y-1.5">
                            <SectionTitle>Что осталось до закрытия</SectionTitle>
                            <div className={`${iosCard} px-4 py-2.5`}>
                                <ul className="space-y-1">
                                    {(data.closing || []).map((item) => (
                                        <li key={item.label} className={`flex items-center gap-2 text-[13px] ${item.ok ? 'text-slate-500' : 'text-slate-900'}`}>
                                            {item.ok
                                                ? <Check size={14} strokeWidth={3} className="shrink-0 text-emerald-600" />
                                                : <span className="h-3.5 w-3.5 shrink-0 rounded-full ring-1 ring-slate-300" />}
                                            {item.label}
                                        </li>
                                    ))}
                                </ul>
                            </div>
                        </section>
                    )}

                    <section className="space-y-1.5">
                        <SectionTitle>Ход заявки</SectionTitle>
                        <div className={`${iosCard} p-3.5`}>
                            <ol>{subtasks.filter((item) => item.kind !== 'initiation').map((item, index, list) => renderSubtask(item, index === list.length - 1))}</ol>
                        </div>
                    </section>

                    {((data.attachments || []).length > 0 || permissions.can_attach) && (
                        <section className="space-y-1.5">
                            <SectionTitle>Документы</SectionTitle>
                            <div className={`${iosCard} p-2`}>
                                <AttachmentList attachments={data.attachments} apiBaseUrl={apiBaseUrl} headers={headers} canRemove={canRemoveAttachment} onRemove={removeAttachment} showToast={showToast} showStep />
                                {permissions.can_attach && (
                                    <div className="px-2 pt-1">
                                        <FilePicker files={extraFiles} onChange={setExtraFiles} kinds={fileKindOptions(closingStarted ? 'closing_docs' : 'request')} label="Добавить документ" />
                                        {extraFiles.length > 0 && (
                                            <button type="button" className={`${iosBtnPrimary} mb-1 mt-2`} disabled={busy} onClick={() => attach(extraFiles)}>
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
                                        <div className="text-slate-800">{describeEvent(event, titleOf)}{event.actor_name ? <span className="text-slate-500"> · {event.actor_name}</span> : null}</div>
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
