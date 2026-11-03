import React from 'react';
import IosDatePicker from '../ui/DatePicker';
import InfoHint, { InfoHintLines } from '../common/InfoHint';
import { IosSegmented, iosGroupLabel, iosInput } from '../ui/ios';
import {
    LEAVE_KIND_SEGMENTS,
    LEAVE_SICK_BACK_DAYS,
    addDaysIso,
    daysText,
    leaveEarliestStart,
    leaveKindHints,
    leaveKindsText,
    leavePeriodDays,
} from './leaveRequests';

/*
 * Поля заявки на отпуск, Б/С или больничный — тело того же окна «Новый
 * запрос», что и у заявки по смене (задачи #358, #346). Окно одно: оператор
 * выбирает в «Что нужно» этот пункт, и вместо дня и времени появляются эти
 * поля. Второе окно поверх первого или подмена окна на лету дали бы рывок.
 *
 * Вид — сегментами (два-три варианта, все словами). Пока не выбран, не
 * подсвечен ни один: «не выбрано» — не «отпуск по умолчанию». Если отдел
 * принимает один вид, выбора нет вовсе — вопрос с одним ответом только шумит.
 *
 * Последний день подстраивается под первый: выбрал начало позже конца или
 * конца ещё нет — конец встаёт на тот же день, и период сразу корректен.
 * Больничный календарь пускает и в прошлое: болезнь не планируют.
 */
const REASON_PLACEHOLDERS = {
    annual_leave: 'Например: поездка к родным',
    bs: 'Например: семейные обстоятельства',
    sick_leave: 'Например: открыт больничный лист. Диагноз писать не нужно',
};

export default function LeaveRequestFields({
    draft,
    onChange,
    kinds = [],
    today,
    maxPeriodDays = 365,
    maxAheadDays = 365,
    sickBackDays = LEAVE_SICK_BACK_DAYS,
}) {
    const set = (patch) => onChange({ ...draft, ...patch });
    const days = leavePeriodDays(draft.startDate, draft.endDate);
    const firstStart = leaveEarliestStart(draft.kind, today, sickBackDays);
    const lastStart = addDaysIso(today, maxAheadDays);
    const lastEnd = draft.startDate
        ? addDaysIso(draft.startDate, maxPeriodDays - 1)
        : addDaysIso(lastStart, maxPeriodDays - 1);

    return (
        <>
            {kinds.length > 1 && (
                <div className="space-y-1.5">
                    <div className="flex items-center gap-1.5">
                        <span className={iosGroupLabel}>Вид</span>
                        <InfoHint side="left">
                            <InfoHintLines lines={leaveKindHints(kinds)} />
                        </InfoHint>
                    </div>
                    <IosSegmented
                        value={draft.kind}
                        onChange={(kind) => set({ kind })}
                        stretch
                        ariaLabel={leaveKindsText(kinds, 'или')}
                        options={kinds.map((kind) => ({ value: kind, label: LEAVE_KIND_SEGMENTS[kind] || kind }))}
                    />
                </div>
            )}

            <div className="space-y-1.5">
                <div className="grid grid-cols-2 gap-3">
                    <div className="space-y-1.5">
                        <div className={iosGroupLabel}>Первый день</div>
                        <IosDatePicker
                            value={draft.startDate}
                            onChange={(value) => set({
                                startDate: value,
                                endDate: !draft.endDate || draft.endDate < value ? value : draft.endDate,
                            })}
                            min={firstStart}
                            max={lastStart}
                            placeholder="Выберите"
                            ariaLabel="Первый день"
                        />
                    </div>
                    <div className="space-y-1.5">
                        <div className={iosGroupLabel}>Последний день</div>
                        <IosDatePicker
                            value={draft.endDate}
                            onChange={(value) => set({ endDate: value })}
                            min={draft.startDate || firstStart}
                            max={lastEnd}
                            placeholder="Выберите"
                            ariaLabel="Последний день"
                        />
                    </div>
                </div>
                {/* Число дней — само (ТЗ п. 1). Календарных: так считает и график,
                    и праздники в нём отдельно не отмечены. */}
                {days > 0 && (
                    <div className="px-1 text-[12px] tabular-nums text-slate-500">
                        <span className="font-semibold text-slate-700">{daysText(days)}</span>
                        <span className="text-slate-400"> · календарных</span>
                    </div>
                )}
            </div>

            <div className="space-y-1.5">
                <div className={iosGroupLabel}>Причина</div>
                <textarea
                    rows={2}
                    value={draft.reason}
                    onChange={(event) => set({ reason: event.target.value })}
                    placeholder={REASON_PLACEHOLDERS[draft.kind] || REASON_PLACEHOLDERS.annual_leave}
                    aria-label="Причина заявки"
                    maxLength={1000}
                    className={`${iosInput} resize-none`}
                />
            </div>
        </>
    );
}
