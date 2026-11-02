import React from 'react';
import { CalendarMinus2, TreePalm } from 'lucide-react';
import { IosBadge, iosCard, iosGroupLabel } from '../ui/ios';
import ShiftRequestComment from '../schedule/ShiftRequestComment';
import { MyShiftsRequestCard, PhoneGroup } from '../schedule/MyShiftsMobile';
import {
    LEAVE_KIND_LABELS,
    LEAVE_STATUS_META,
    daysText,
    formatLeavePeriod,
    leavePeriodDays,
} from './leaveRequests';

/*
 * Свои заявки оператора на отпуск и Б/С — группа во вкладке «Запросы» «Моих
 * смен», над заявками по сменам (задача #358). Два вида одной группы: строки
 * карточки на компьютере и карточки списка на телефоне (MyShiftsRequestCard —
 * та же разметка, что у заявок по сменам).
 *
 * Предложенные супервайзером даты — единственное, что ждёт ответа самого
 * оператора, поэтому они стоят отдельным голубым блоком с двумя кнопками, а не
 * ещё одной серой строкой.
 */

const PHONE_TILES = {
    annual_leave: { icon: TreePalm, tile: 'bg-sky-500' },
    bs: { icon: CalendarMinus2, tile: 'bg-violet-500' },
};

const periodText = (start, end, today) => `${formatLeavePeriod(start, end, today)} · ${daysText(leavePeriodDays(start, end))}`;

const proposerName = (item) => item?.proposal?.by?.name || 'Супервайзер';

const SMALL_PRIMARY = 'rounded-lg bg-blue-600 px-3 py-1.5 text-[12.5px] font-semibold text-white transition hover:bg-blue-700 active:scale-[0.98] disabled:opacity-50';
const SMALL_SECONDARY = 'rounded-lg bg-white px-3 py-1.5 text-[12.5px] font-semibold text-slate-600 ring-1 ring-slate-200 transition hover:bg-slate-50 active:scale-[0.98] disabled:opacity-50';
const ROW_ACTION = 'shrink-0 rounded-lg px-2.5 py-1 text-[12px] font-medium text-slate-500 transition hover:bg-slate-100 hover:text-slate-700 active:scale-[0.98] disabled:opacity-50';

function ProposalBox({ item, today, busy, onAccept, onDecline }) {
    const proposal = item.proposal;
    return (
        <div className="mt-2.5 rounded-xl bg-blue-50/70 px-3.5 py-2.5 ring-1 ring-blue-100">
            <div className="text-[12.5px] leading-snug text-slate-600">
                {proposerName(item)} предлагает другие даты
            </div>
            <div className="mt-0.5 text-[14px] font-semibold tabular-nums text-slate-900">
                {periodText(proposal.startDate, proposal.endDate, today)}
            </div>
            <ShiftRequestComment text={proposal.comment} className="mt-1.5 bg-white" />
            <div className="mt-2.5 flex flex-wrap items-center gap-2">
                <button type="button" className={SMALL_PRIMARY} disabled={busy} onClick={() => onAccept(item)}>
                    Согласен
                </button>
                <button type="button" className={SMALL_SECONDARY} disabled={busy} onClick={() => onDecline(item)}>
                    Не согласен
                </button>
                <span className="text-[11.5px] leading-snug text-slate-500">
                    Согласитесь — заявка уйдёт руководителю с этими датами
                </span>
            </div>
        </div>
    );
}

function DesktopRow({ item, today, busy, onAccept, onDecline, onEdit, onCancel }) {
    const meta = LEAVE_STATUS_META[item.status] || LEAVE_STATUS_META.supervisor;
    const actions = item.actions || [];
    return (
        <div className="px-4 py-3">
            <div className="flex flex-wrap items-start justify-between gap-x-3 gap-y-1.5">
                <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                        <span className="text-[14px] font-semibold text-slate-900">
                            {LEAVE_KIND_LABELS[item.kind] || 'Заявка'}
                        </span>
                        <IosBadge tone={meta.tone}>{meta.label}</IosBadge>
                    </div>
                    <div className="mt-1 text-[12.5px] tabular-nums text-slate-600">
                        {periodText(item.startDate, item.endDate, today)}
                    </div>
                    <ShiftRequestComment text={item.reason} className="mt-1.5" />
                    {item.decisionComment && (
                        <div className="mt-1 text-[12px] leading-snug text-slate-600">
                            <span className="text-slate-400">Ответ: </span>
                            {item.decisionComment}
                        </div>
                    )}
                </div>
                {(actions.includes('edit') || actions.includes('cancel')) && (
                    <div className="flex shrink-0 items-center gap-1">
                        {actions.includes('edit') && (
                            <button type="button" className={ROW_ACTION} disabled={busy} onClick={() => onEdit(item)}>
                                Изменить
                            </button>
                        )}
                        {actions.includes('cancel') && (
                            <button type="button" className={ROW_ACTION} disabled={busy} onClick={() => onCancel(item)}>
                                {busy ? 'Минуту…' : 'Отозвать'}
                            </button>
                        )}
                    </div>
                )}
            </div>
            {item.status === 'proposal' && item.proposal && (
                <ProposalBox item={item} today={today} busy={busy} onAccept={onAccept} onDecline={onDecline} />
            )}
        </div>
    );
}

export function LeaveRequestListDesktop({ items = [], today, busyId = null, onAccept, onDecline, onEdit, onCancel }) {
    if (!items.length) return null;
    return (
        <section className="space-y-1.5">
            <div className={iosGroupLabel}>Отпуск и Б/С</div>
            <div className={`${iosCard} divide-y divide-slate-100 overflow-hidden`}>
                {items.map((item) => (
                    <DesktopRow
                        key={`leave-${item.id}`}
                        item={item}
                        today={today}
                        busy={busyId === item.id}
                        onAccept={onAccept}
                        onDecline={onDecline}
                        onEdit={onEdit}
                        onCancel={onCancel}
                    />
                ))}
            </div>
        </section>
    );
}

export function LeaveRequestListPhone({ items = [], today, busyId = null, onAccept, onDecline, onEdit, onCancel }) {
    if (!items.length) return null;
    return (
        <PhoneGroup label="Отпуск и Б/С">
            {items.map((item) => {
                const meta = LEAVE_STATUS_META[item.status] || LEAVE_STATUS_META.supervisor;
                const tile = PHONE_TILES[item.kind] || PHONE_TILES.annual_leave;
                const busy = busyId === item.id;
                const actions = item.actions || [];
                const proposal = item.status === 'proposal' ? item.proposal : null;
                const pills = proposal ? [
                    { key: 'accept', label: 'Согласен', tone: 'blue', busy, disabled: busy, onClick: () => onAccept(item) },
                    { key: 'decline', label: 'Не согласен', tone: 'slate', disabled: busy, onClick: () => onDecline(item) },
                ] : [
                    actions.includes('edit') ? { key: 'edit', label: 'Изменить', tone: 'slate', disabled: busy, onClick: () => onEdit(item) } : null,
                    actions.includes('cancel') ? { key: 'cancel', label: 'Отозвать', tone: 'rose', busy, disabled: busy, onClick: () => onCancel(item) } : null,
                ].filter(Boolean);
                return (
                    <MyShiftsRequestCard
                        key={`phone-leave-${item.id}`}
                        icon={tile.icon}
                        tileClassName={tile.tile}
                        title={LEAVE_KIND_LABELS[item.kind] || 'Заявка'}
                        badge={meta.short}
                        badgeTone={meta.tone}
                        subtitle={periodText(item.startDate, item.endDate, today)}
                        comment={item.reason}
                        notes={[
                            proposal ? `${proposerName(item)} предлагает: ${periodText(proposal.startDate, proposal.endDate, today)}` : null,
                            proposal?.comment ? `«${proposal.comment}»` : null,
                            item.decisionComment ? `Ответ: ${item.decisionComment}` : null,
                        ]}
                        actions={pills}
                    />
                );
            })}
        </PhoneGroup>
    );
}
