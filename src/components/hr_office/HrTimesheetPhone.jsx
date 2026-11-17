import React from 'react';
import { ChevronRight } from 'lucide-react';
import { IosModal, iosCard } from '../ui/ios';
import { cellTone } from './hrOffice';
import { CELL_TONE, Empty } from './hrUi';

/*
 * Табель на телефоне (владелец 12.11.2026: «адаптив … и для телефона тоже»). Таблица на 31 день в
 * 390 px — это колонка имён и дни за краем экрана, поэтому на узком экране табель — список людей с
 * итогами месяца, а месяц человека — отдельным экраном: день строкой, значение и минуты опоздания,
 * правка — тем же окном ячейки, что на компьютере. Сетка (flex, а не grid-cols-[…]) — оболочка
 * телефона (mobile-shell.css) схлопывает произвольные сетки в одну колонку.
 */

const MONTH_DAY = { ПН: 'пн', ВТ: 'вт', СР: 'ср', ЧТ: 'чт', ПТ: 'пт', СБ: 'сб', ВС: 'вс' };

function totalsLine(person) {
    const { totals } = person;
    const parts = [`${totals.shifts} смен`, `${String(totals.hours).replace('.', ',')} ч`];
    if (totals.absences) parts.push(`пропусков ${totals.absences}`);
    return parts.join(' · ');
}

/* Люди табеля строками: имя, должность и вид оформления, итоги месяца; нажатие — месяц человека. */
export function TimesheetPhoneList({ groups, onOpen }) {
    if (!groups.length) return null;
    return (
        <div className="divide-y divide-slate-100">
            {groups.map((group) => (
                <React.Fragment key={group.name}>
                    <div className="bg-slate-50/80 px-4 py-2 text-[11.5px] font-semibold uppercase tracking-wide text-slate-500">
                        {group.name} · {group.count}
                    </div>
                    {group.people.map((person) => (
                        <button key={person.user_id} type="button" onClick={() => onOpen(person.user_id)}
                                className="flex w-full items-center gap-3 px-4 py-3 text-left transition active:bg-slate-50">
                            <span className="min-w-0 grow basis-0">
                                <span className="block truncate text-[14.5px] font-medium text-slate-900">{person.name}</span>
                                <span className="block truncate text-[12.5px] text-slate-500">
                                    {[person.job_title, person.contract_label].filter(Boolean).join(' · ') || '—'}
                                </span>
                                <span className="mt-0.5 block truncate text-[12.5px] tabular-nums text-slate-600">
                                    {totalsLine(person)}
                                    {person.totals.minutes < 0 && (
                                        <span className="font-medium text-rose-500"> · {person.totals.minutes} мин</span>
                                    )}
                                </span>
                            </span>
                            <ChevronRight size={18} className="shrink-0 text-slate-300" />
                        </button>
                    ))}
                </React.Fragment>
            ))}
        </div>
    );
}

function Stat({ label, value, tone = 'text-slate-900' }) {
    return (
        <div className="min-w-0 grow basis-0 rounded-2xl bg-white px-2 py-2.5 text-center ring-1 ring-slate-200/60">
            <div className={`text-[17px] font-semibold tabular-nums ${tone}`}>{value}</div>
            <div className="truncate text-[11px] text-slate-500">{label}</div>
        </div>
    );
}

/* Месяц человека экраном: итоги плитками, дни строками; день и комментарий правятся окнами табеля. */
export function PersonMonthSheet({ open = true, model, person, editable, onClose, onEditCell, onEditNote }) {
    if (!person) return null;
    const comment = person.note || person.note_auto || '';
    return (
        <IosModal open={open} onClose={onClose} maxWidth="max-w-lg" title={person.name}
                  subtitle={[person.job_title, person.contract_label].filter(Boolean).join(' · ') || model.title}>
            <div className="flex flex-col gap-3">
                <div className="px-1 text-[13px] text-slate-500">
                    {model.title} · план {person.plan_days} дн · {String(person.plan_hours).replace('.', ',')} ч{person.schedule ? ` · ${person.schedule}` : ''}
                </div>
                <div className="flex gap-2">
                    <Stat label="смен" value={person.totals.shifts} />
                    <Stat label="часов" value={String(person.totals.hours).replace('.', ',')} />
                    <Stat label="пропусков" value={person.totals.absences} />
                    <Stat label="минут" value={person.totals.minutes}
                          tone={person.totals.minutes < 0 ? 'text-rose-500' : 'text-slate-900'} />
                </div>
                <button type="button" disabled={!editable} onClick={() => onEditNote(person)}
                        className={`${iosCard} flex w-full items-center gap-3 px-4 py-3 text-left disabled:opacity-100`}>
                    <span className="min-w-0 grow basis-0">
                        <span className="block text-[12px] font-medium text-slate-500">Комментарий</span>
                        <span className={`block text-[14px] ${comment ? 'text-slate-800' : 'text-slate-400'}`}>
                            {comment || (editable ? 'Добавить' : '—')}
                        </span>
                    </span>
                    {editable && <ChevronRight size={18} className="shrink-0 text-slate-300" />}
                </button>
                <div className={`${iosCard} divide-y divide-slate-100 overflow-hidden`}>
                    {person.cells.map((cell, index) => {
                        const day = model.days[index];
                        const off = day.weekend || day.holiday;
                        const tone = cellTone(cell.v);
                        const minutes = cell.m !== null && cell.m !== undefined && cell.m !== '' ? cell.m : null;
                        return (
                            <button key={day.date} type="button" disabled={!editable} onClick={() => onEditCell(person, day, cell)}
                                    className="relative flex w-full items-center gap-3 px-4 py-2.5 text-left transition active:bg-slate-50 disabled:active:bg-transparent">
                                <span className={`w-14 shrink-0 tabular-nums ${off ? 'text-slate-400' : 'text-slate-700'}`}>
                                    <span className="text-[15px] font-medium">{day.day}</span>
                                    <span className="ml-1 text-[12px]">{MONTH_DAY[day.weekday] || day.weekday}</span>
                                </span>
                                <span className="min-w-0 grow basis-0">
                                    {cell.out && !cell.v
                                        ? <span className="text-[13px] text-slate-400">{cell.out === 'hire' ? 'до приема' : 'после увольнения'}</span>
                                        : <span className={CELL_TONE[tone]}>{cell.v || '·'}</span>}
                                    {day.holiday && <span className="ml-2 text-[11.5px] text-rose-400">праздник</span>}
                                </span>
                                {minutes !== null && (
                                    <span className={`shrink-0 text-[13px] tabular-nums ${typeof minutes === 'number' && minutes < 0 ? 'text-rose-500' : 'text-slate-500'}`}>
                                        {typeof minutes === 'number' ? `${minutes} мин` : minutes}
                                    </span>
                                )}
                                {cell.edited && <span className="absolute left-1.5 top-1/2 h-1.5 w-1.5 -translate-y-1/2 rounded-full bg-blue-500" title="Правка HR" />}
                                {cell.no_marks && !cell.edited && (
                                    <span className="absolute left-1.5 top-1/2 h-1.5 w-1.5 -translate-y-1/2 rounded-full bg-amber-400" title="Нет отметок в «Отметках»" />
                                )}
                            </button>
                        );
                    })}
                    {!person.cells.length && <Empty>Дней нет</Empty>}
                </div>
            </div>
        </IosModal>
    );
}
