import React, { useEffect, useMemo, useState } from 'react';
import { AlertTriangle, Inbox, Loader2, RotateCw, Settings2 } from 'lucide-react';
import IosDatePicker from '../ui/DatePicker';
import {
    IosBadge,
    IosHint,
    IosSegmented,
    IosToggle,
    iosBtnGhost,
    iosCard,
    iosGroupLabel,
    iosInput,
} from '../ui/ios';
import ShiftRequestComment from '../schedule/ShiftRequestComment';
import {
    LEAVE_EVENT_LABELS,
    LEAVE_KIND_LABELS,
    LEAVE_OPEN_STATUSES,
    LEAVE_STATUS_META,
    addDaysIso,
    daysText,
    formatLeavePeriod,
    leaveApiError,
    leavePeriodDays,
} from './leaveRequests';

/*
 * Очередь «Отпуск и Б/С» — вкладка окна «Запросы» в «Графиках работы»
 * (задача #358). Разбирают супервайзер (первый шаг) и глава отдела
 * (утверждение). Что кому можно, решает сервер и присылает в item.actions:
 * здесь кнопки только рисуются.
 *
 * Пересечения (ТЗ п. 4) стоят жёлтым блоком прямо в заявке: это то, ради
 * чего руководитель останавливается, а «никто не отдыхает» не пишем вовсе —
 * обычное состояние не красим и не подписываем.
 */

const QUEUE_HINT = 'Заявку сначала согласует супервайзер, затем утверждает руководитель отдела — после этого период сам встаёт в график оператора, а смены на эти дни снимаются. Супервайзер может предложить другие даты: заявка вернётся оператору, и он ответит, согласен ли. Руководитель и администратор могут утвердить заявку сразу, не дожидаясь супервайзера. Жёлтым показано, кто из отдела уже отдыхает в эти даты — по утверждённым отпускам и Б/С в графике.';

const TENURE_HINT = 'Период Б/С не войдёт в стаж: «Стаж» в профиле и стаж в расчётах начнутся позже даты найма на эти дни. По настройке отдела переключатель сам включён для длительного Б/С — проверьте и решите сами.';

const BTN_REJECT = 'rounded-xl bg-slate-100 px-3.5 py-2 text-[13px] font-semibold text-slate-600 transition-all hover:bg-slate-200 active:scale-[0.98] disabled:opacity-50';
const BTN_SECONDARY = 'rounded-xl bg-white px-3.5 py-2 text-[13px] font-semibold text-slate-700 ring-1 ring-slate-200 transition-all hover:bg-slate-50 active:scale-[0.98] disabled:opacity-50';
const BTN_APPROVE = 'rounded-xl bg-blue-600 px-3.5 py-2 text-[13px] font-semibold text-white shadow-sm transition-all hover:bg-blue-700 active:scale-[0.98] disabled:opacity-50';

const fmtDateTime = (iso) => {
    const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(String(iso || ''));
    return match ? `${match[3]}.${match[2]} ${match[4]}:${match[5]}` : '';
};

function OverlapNote({ overlaps, today, title = 'В эти даты уже отдыхают' }) {
    const total = Number(overlaps?.total) || 0;
    if (!total) return null;
    const items = Array.isArray(overlaps?.items) ? overlaps.items : [];
    const rest = total - items.length;
    return (
        <div className="mt-2 rounded-xl bg-amber-50 px-3 py-2 ring-1 ring-amber-200/70">
            <div className="flex items-center gap-1.5 text-[12.5px] font-semibold text-amber-800">
                <AlertTriangle size={14} className="shrink-0" aria-hidden="true" />
                {title}: {total}
            </div>
            <ul className="mt-1 space-y-0.5">
                {items.map((row) => (
                    <li key={`${row.operatorId}-${row.startDate}`} className="text-[12px] leading-snug tabular-nums text-amber-900/80">
                        <span className="font-medium text-amber-900">{row.name}</span>
                        {' · '}{LEAVE_KIND_LABELS[row.kind] || row.kindLabel}
                        {' · '}{formatLeavePeriod(row.startDate, row.endDate, today)}
                        {row.sameGroup ? <span className="text-amber-700"> · своя группа</span> : null}
                    </li>
                ))}
            </ul>
            {rest > 0 && <div className="mt-0.5 text-[12px] text-amber-800/80">и ещё {rest}</div>}
        </div>
    );
}

// Последний шаг заявки одной строкой: кто и что сделал. Подача не нужна —
// она и так видна по заявке; предложение дат — своей строкой выше.
function LastStep({ item }) {
    if (item.status === 'proposal') return null;
    const events = Array.isArray(item.events) ? item.events : [];
    const last = [...events].reverse().find((event) => event.action !== 'created');
    if (!last) return null;
    return (
        <div className="mt-1 text-[11.5px] text-slate-400">
            {LEAVE_EVENT_LABELS[last.action] || last.action}
            {last.actor?.name ? ` · ${last.actor.name}` : ''}
            {last.at ? ` · ${fmtDateTime(last.at)}` : ''}
            {last.comment && last.action !== 'edited' ? <ShiftRequestComment text={last.comment} className="mt-1" /> : null}
        </div>
    );
}

function ProposeEditor({ api, item, today, busy, onCancel, onSubmit }) {
    const [start, setStart] = useState(item.startDate);
    const [end, setEnd] = useState(item.endDate);
    const [comment, setComment] = useState('');
    const [preview, setPreview] = useState(null);
    const [previewLoading, setPreviewLoading] = useState(false);

    // Кто отдыхает в НОВЫЕ даты — до того, как предложить их оператору.
    useEffect(() => {
        if (!start || !end || end < start) { setPreview(null); return undefined; }
        let alive = true;
        setPreviewLoading(true);
        const timer = setTimeout(() => {
            api.overlaps(item.id, start, end)
                .then((data) => { if (alive) setPreview(data); })
                .catch(() => { if (alive) setPreview(null); })
                .finally(() => { if (alive) setPreviewLoading(false); });
        }, 250);
        return () => { alive = false; clearTimeout(timer); };
    }, [api, item.id, start, end]);

    const same = start === item.startDate && end === item.endDate;
    const days = leavePeriodDays(start, end);
    const problem = !start || !end ? 'Выберите даты'
        : end < start ? 'Последний день раньше первого'
            : same ? 'Это те же даты, что в заявке' : '';

    return (
        <div className="mt-2.5 space-y-2.5 rounded-xl bg-slate-50 px-3.5 py-3 ring-1 ring-slate-200/70">
            <div className="text-[12.5px] font-semibold text-slate-700">Предложить другие даты</div>
            <div className="grid grid-cols-2 gap-3">
                <div className="space-y-1">
                    <div className={iosGroupLabel}>Первый день</div>
                    <IosDatePicker
                        value={start}
                        onChange={(value) => { setStart(value); if (!end || end < value) setEnd(value); }}
                        min={today}
                        max={addDaysIso(today, 365)}
                        ariaLabel="Первый день"
                    />
                </div>
                <div className="space-y-1">
                    <div className={iosGroupLabel}>Последний день</div>
                    <IosDatePicker
                        value={end}
                        onChange={setEnd}
                        min={start || today}
                        max={start ? addDaysIso(start, 364) : addDaysIso(today, 729)}
                        ariaLabel="Последний день"
                    />
                </div>
            </div>
            <div className="flex items-center gap-1.5 px-1 text-[12px] tabular-nums text-slate-500">
                {days > 0 && <span className="font-semibold text-slate-700">{daysText(days)}</span>}
                {previewLoading && <Loader2 size={12} className="animate-spin text-slate-400" aria-hidden="true" />}
            </div>
            <OverlapNote overlaps={preview} today={today} title="В новые даты отдыхают" />
            <input
                type="text"
                value={comment}
                onChange={(event) => setComment(event.target.value)}
                placeholder="Почему эти даты · необязательно"
                maxLength={1000}
                className={`${iosInput} !py-2 !text-[13px]`}
            />
            <div className="flex flex-wrap items-center justify-end gap-2">
                {problem && <span className="mr-auto text-[12px] text-slate-500">{problem}</span>}
                <button type="button" className={BTN_REJECT} disabled={busy} onClick={onCancel}>Отмена</button>
                <button
                    type="button"
                    className={BTN_APPROVE}
                    disabled={busy || Boolean(problem)}
                    onClick={() => onSubmit({ action: 'propose', start_date: start, end_date: end, comment })}
                >
                    {busy ? 'Отправляем…' : 'Предложить оператору'}
                </button>
            </div>
        </div>
    );
}

function ReviewItem({ api, item, today, onDecided, onError }) {
    const [comment, setComment] = useState('');
    const [proposing, setProposing] = useState(false);
    const [busy, setBusy] = useState(false);
    const [tenure, setTenure] = useState(Boolean(item.tenureDefault));
    useEffect(() => { setTenure(Boolean(item.tenureDefault)); }, [item.id, item.tenureDefault]);
    // Глава может решить заявку, которая ещё у супервайзера, но это обход шага:
    // кнопки такой заявки спрятаны за отдельным действием, а не стоят рядом с
    // его собственной очередью.
    const [bypass, setBypass] = useState(false);

    const meta = LEAVE_STATUS_META[item.status] || LEAVE_STATUS_META.supervisor;
    const actions = item.actions || [];
    const isOpen = LEAVE_OPEN_STATUSES.includes(item.status);
    const showTenure = actions.includes('approve') && item.approveIsFinal && item.kind === 'bs';

    const send = async (body) => {
        setBusy(true);
        try {
            const data = await api.decide(item.id, body);
            setProposing(false);
            setComment('');
            onDecided(data?.request || null, body.action);
        } catch (err) {
            onError(leaveApiError(err, 'Не удалось обработать заявку'));
        } finally {
            setBusy(false);
        }
    };

    const approve = () => send({
        action: 'approve',
        comment: comment.trim() || undefined,
        ...(showTenure ? { exclude_from_tenure: tenure } : {}),
    });

    return (
        <div className="px-4 py-3">
            <div className="flex flex-wrap items-start justify-between gap-x-3 gap-y-1">
                <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                        <span className="text-[14px] font-semibold text-slate-900">{item.operator?.name || 'Сотрудник'}</span>
                        {/* Шаг пишем, когда заявка ждёт не вас: своя очередь и так
                            понятна по кнопкам. */}
                        {(!item.awaitsMe || !isOpen) && <IosBadge tone={meta.tone}>{meta.label}</IosBadge>}
                    </div>
                    <div className="mt-0.5 text-[12.5px] text-slate-500">
                        {LEAVE_KIND_LABELS[item.kind] || 'Заявка'}
                        {item.group?.name ? <><span className="text-slate-300"> · </span>{item.group.name}</> : null}
                        {item.operator?.direction ? <><span className="text-slate-300"> · </span>{item.operator.direction}</> : null}
                    </div>
                    <div className="mt-1 text-[13px] font-medium tabular-nums text-slate-800">
                        {formatLeavePeriod(item.startDate, item.endDate, today)}
                        <span className="font-normal text-slate-500"> · {daysText(item.days || leavePeriodDays(item.startDate, item.endDate))}</span>
                    </div>
                    <ShiftRequestComment text={item.reason} className="mt-1.5" />
                    {item.status === 'proposal' && item.proposal && (
                        <>
                            <div className="mt-1.5 text-[12.5px] tabular-nums text-blue-700">
                                {item.proposal.by?.name || 'Супервайзер'} предложил(а){' '}
                                {formatLeavePeriod(item.proposal.startDate, item.proposal.endDate, today)}
                                {' · '}{daysText(item.proposal.days)} — ждём ответа оператора
                            </div>
                            <ShiftRequestComment text={item.proposal.comment} className="mt-1" />
                        </>
                    )}
                    {item.status === 'approved' && item.excludeFromTenure && (
                        <div className="mt-1 text-[12px] text-slate-500">Не учитывается в стаже</div>
                    )}
                    <LastStep item={item} />
                </div>
            </div>

            {isOpen && <OverlapNote overlaps={item.overlaps} today={today} />}

            {proposing ? (
                <ProposeEditor api={api} item={item} today={today} busy={busy} onCancel={() => setProposing(false)} onSubmit={send} />
            ) : actions.length > 0 && !item.awaitsMe && !bypass ? (
                <button type="button" className={`${iosBtnGhost} mt-1.5 -ml-3`} onClick={() => setBypass(true)}>
                    Решить, не дожидаясь супервайзера
                </button>
            ) : actions.length > 0 && (
                <div className="mt-2.5 space-y-2">
                    {showTenure && (
                        <div className="flex items-center justify-between gap-3 rounded-xl bg-slate-50 px-3 py-2 ring-1 ring-slate-200/70">
                            <div className="min-w-0 text-[12.5px] leading-snug text-slate-600">
                                Не учитывать в стаже
                                <span className="ml-1.5 inline-flex align-middle"><IosHint align="left" text={TENURE_HINT} /></span>
                            </div>
                            <IosToggle checked={tenure} onChange={setTenure} disabled={busy} />
                        </div>
                    )}
                    <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
                        <input
                            type="text"
                            value={comment}
                            onChange={(event) => setComment(event.target.value)}
                            placeholder="Комментарий · необязательно"
                            maxLength={1000}
                            className={`${iosInput} flex-1 !py-2 !text-[13px]`}
                        />
                        <div className="flex shrink-0 flex-wrap gap-2">
                            {actions.includes('reject') && (
                                <button type="button" className={BTN_REJECT} disabled={busy}
                                        onClick={() => send({ action: 'reject', comment: comment.trim() || undefined })}>
                                    Отклонить
                                </button>
                            )}
                            {actions.includes('propose') && (
                                <button type="button" className={BTN_SECONDARY} disabled={busy} onClick={() => setProposing(true)}>
                                    Другие даты
                                </button>
                            )}
                            {actions.includes('approve') && (
                                <button type="button" className={BTN_APPROVE} disabled={busy} onClick={approve}>
                                    {busy ? 'Минуту…' : (item.approveIsFinal ? 'Утвердить' : 'Согласовать')}
                                </button>
                            )}
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}

export default function LeaveReviewPanel({
    api,
    review,
    statusFilter,
    onStatusFilter,
    onDecided,
    onOpenSettings,
}) {
    const [actionError, setActionError] = useState('');
    const { requests, loading, error, reload, canManageSettings } = review;
    const today = review.today;

    // Очередь делится на «ждут вас» и остальное: своё — первым, без поиска глазами.
    const groups = useMemo(() => {
        const mine = requests.filter((item) => item.awaitsMe);
        const others = requests.filter((item) => !item.awaitsMe);
        return [
            { key: 'mine', label: 'Ждут вашего решения', items: mine },
            { key: 'others', label: statusFilter === 'pending' ? 'На других шагах' : 'Остальные', items: others },
        ].filter((group) => group.items.length > 0);
    }, [requests, statusFilter]);

    return (
        <div className="space-y-3">
            <div className="flex items-center justify-between gap-2">
                <IosSegmented
                    value={statusFilter}
                    onChange={onStatusFilter}
                    ariaLabel="Какие заявки показывать"
                    options={[
                        { value: 'pending', label: 'Ожидают' },
                        { value: 'all', label: 'Все' },
                    ]}
                />
                <div className="flex items-center gap-1.5">
                    <IosHint align="right" text={QUEUE_HINT} />
                    {canManageSettings && (
                        <button type="button" className={`${iosBtnGhost} shrink-0`} onClick={onOpenSettings} title="Какие отделы принимают заявки">
                            <Settings2 size={13} className="text-slate-400" aria-hidden="true" />
                            Настройки
                        </button>
                    )}
                    <button type="button" className={`${iosBtnGhost} shrink-0`} disabled={loading} onClick={() => reload({ silent: false })}>
                        {loading
                            ? <Loader2 size={12} className="animate-spin text-slate-400" aria-hidden="true" />
                            : <RotateCw size={12} className="text-slate-400" aria-hidden="true" />}
                        Обновить
                    </button>
                </div>
            </div>

            {(actionError || error) && (
                <div className="rounded-xl bg-rose-50 px-3.5 py-2.5 text-[12.5px] text-rose-700 ring-1 ring-rose-200/70">
                    {actionError || error}
                </div>
            )}

            {loading && requests.length === 0 ? (
                <div className="px-4 py-10 text-center text-[13px] text-slate-500">
                    <Loader2 size={14} className="mr-2 inline animate-spin text-slate-400" aria-hidden="true" />
                    Загружаем заявки…
                </div>
            ) : requests.length === 0 ? (
                <div className="px-4 py-10 text-center">
                    <div className="mx-auto mb-2 grid h-11 w-11 place-items-center rounded-2xl bg-slate-100 text-slate-400">
                        <Inbox size={18} aria-hidden="true" />
                    </div>
                    <div className="text-[14px] font-semibold text-slate-700">
                        {statusFilter === 'pending' ? 'Всё разобрано' : 'Заявок нет'}
                    </div>
                    <div className="mt-0.5 text-[12.5px] text-slate-500">
                        {statusFilter === 'pending'
                            ? 'Новые заявки на отпуск и Б/С появятся здесь и в колоколе.'
                            : 'Операторы ещё не подавали заявок на отпуск и Б/С.'}
                    </div>
                </div>
            ) : (
                <div className="space-y-3">
                    {groups.map((group) => (
                        <section key={group.key}>
                            {groups.length > 1 && (
                                <div className="mb-1.5 flex items-baseline justify-between gap-2 px-1">
                                    <span className="truncate text-[12.5px] font-semibold text-slate-700">{group.label}</span>
                                    <span className="shrink-0 text-[12px] tabular-nums text-slate-400">{group.items.length}</span>
                                </div>
                            )}
                            <div className={`${iosCard} divide-y divide-slate-100 overflow-hidden`}>
                                {group.items.map((item) => (
                                    <ReviewItem
                                        key={`leave-review-${item.id}`}
                                        api={api}
                                        item={item}
                                        today={today}
                                        onError={setActionError}
                                        onDecided={(updated, action) => {
                                            setActionError('');
                                            onDecided?.(updated, action);
                                        }}
                                    />
                                ))}
                            </div>
                        </section>
                    ))}
                </div>
            )}
        </div>
    );
}
