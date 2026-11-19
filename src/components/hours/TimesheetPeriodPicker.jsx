import React from 'react';
import { ChevronLeft, ChevronRight } from 'lucide-react';
import { IosDateRangePicker } from '../ui/DateRangePicker';
import {
    TIMESHEET_PERIOD_BUTTON_WIDTH,
    TIMESHEET_PERIOD_HINT,
    shiftTimesheetPeriod,
    timesheetPeriodChip,
    timesheetPeriodPresets,
    timesheetSpanLimit,
} from './frontOfficeTimesheet';

/*
 * Период табеля фронт-офисов в шапке «Учета часов» — на месте выбора месяца,
 * пока на экране табель (просьба владельца 19.11.2026: «не только по месяцу,
 * но и по периоду, месяц — это максимум, от 1 дня»).
 *
 * Вид — тот же, что у MonthPicker рядом в режиме часов: стрелки по краям и
 * подпись посередине, без шеврона. Ширина постоянная и та же, что у MonthPicker
 * в этой шапке (TIMESHEET_PERIOD_BUTTON_WIDTH): подпись меняет длину от
 * «5 ноя 2026» до «31 янв – 28 фев 2027», а кнопки слева не должны съезжать —
 * разбор 19.11.2026 намерил сдвиг на 47–55 px при «ширине по подписи».
 *
 * Посередине — общий календарь сайта (IosDateRangePicker) с вкладками
 * «Дни · Месяцы»: месяц целиком — одним нажатием, как раньше, или дни «с — по»;
 * дни дальше месяца от начала погашены. Стрелки листают соседний период того
 * же размера: месяц и «месяц от N-го» — месяцами, день — по дню, неделю — по неделе.
 */
const ARROW_CLASS = 'grid h-[34px] w-[30px] place-items-center bg-white text-slate-400 ring-1 ring-slate-200/70 '
    + 'transition hover:bg-slate-50 hover:text-slate-700 active:scale-[0.97]';
const TRIGGER_CLASS = `relative flex h-[34px] ${TIMESHEET_PERIOD_BUTTON_WIDTH} items-center justify-center gap-2 bg-white px-3 `
    + 'text-[12.5px] font-semibold text-slate-700 ring-1 ring-slate-200/70 transition hover:bg-slate-50 '
    + 'active:scale-[0.99] aria-expanded:z-10 aria-expanded:ring-2 aria-expanded:ring-blue-500/60 '
    + '[&>span]:truncate [&>span]:tabular-nums [&>svg:last-child]:hidden';

export default function TimesheetPeriodPicker({ period, onChange }) {
    const go = (direction) => {
        const next = shiftTimesheetPeriod(period, direction);
        if (next) onChange(next);
    };
    return (
        <div className="flex shrink-0 items-center gap-1">
            <button
                type="button"
                onClick={() => go(-1)}
                title="Предыдущий период"
                aria-label="Предыдущий период"
                className={`${ARROW_CLASS} rounded-l-xl`}
            >
                <ChevronLeft size={15} />
            </button>
            <div style={{ marginLeft: -4, marginRight: -4 }}>
                <IosDateRangePicker
                    from={period?.from || ''}
                    to={period?.to || ''}
                    onChange={(next) => {
                        if (next?.from && next?.to) onChange(next);
                    }}
                    views={['days', 'months']}
                    presets={timesheetPeriodPresets()}
                    spanLimit={timesheetSpanLimit}
                    startHint={TIMESHEET_PERIOD_HINT}
                    formatLabel={(from, to) => timesheetPeriodChip({ from, to })}
                    triggerClassName={TRIGGER_CLASS}
                    portal
                />
            </div>
            <button
                type="button"
                onClick={() => go(1)}
                title="Следующий период"
                aria-label="Следующий период"
                className={`${ARROW_CLASS} rounded-r-xl`}
            >
                <ChevronRight size={15} />
            </button>
        </div>
    );
}
