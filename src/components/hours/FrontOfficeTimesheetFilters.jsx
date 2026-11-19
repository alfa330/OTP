import React, { useId, useMemo, useRef } from 'react';
import CustomSelect from '../ui/CustomSelect';
import { IosDateRangePicker } from '../ui/DateRangePicker';
import HoursFold from './HoursFold';
import {
    TIMESHEET_PERIOD_HINT,
    timesheetPeriodFieldLabel,
    timesheetPeriodPresets,
    timesheetSelectOptions,
    timesheetSpanLimit,
    timesheetWithoutCard,
} from './frontOfficeTimesheet';

/*
 * Отбор табеля фронт-офисов: город, сотрудник, должность (ТЗ, п. 6), а в окне
 * «Сформировать отчёт» ещё и период. Пустой отбор — все: без отборов табель
 * общий по всем городам и сотрудникам, как требует ТЗ.
 *
 * С 19.11.2026 (просьба владельца: период от дня до месяца и «точечно выбирать
 * сотрудников») отбор один на экран и на файл: строкой над табелем
 * (layout="row") и колонкой в окне (layout="stack") — оба вида держат одно и то
 * же состояние раздела, поэтому скачивается ровно то, что видно на экране.
 * Списки (состав периода с сервера) грузит раздел один раз — useTimesheetOptions,
 * сюда они приходят готовыми.
 *
 * Пикеры — общий CustomSelect в виде iOS (эталон пикеров портала), выбор
 * нескольких значений с поиском; период — общий календарь сайта
 * (IosDateRangePicker, «Дни · Месяцы»), дни дальше месяца от начала погашены.
 */
function pickedLabel(options, emptyText) {
    return (values) => {
        const labels = values
            .map((value) => options.find((option) => option.value === value)?.label)
            .filter(Boolean);
        if (!labels.length) return emptyText;
        if (labels.length <= 2) return labels.join(', ');
        return `Выбрано: ${labels.length}`;
    };
}

function PickerRow({ id, label, children }) {
    return (
        <div>
            <label htmlFor={id} className="mb-1 block text-[12.5px] font-medium text-slate-600">{label}</label>
            {children}
        </div>
    );
}

// Поле периода в окне — во всю ширину, как списки под ним (тот же вид, что у
// CustomSelect variant="ios"); шеврон справа.
const PERIOD_FIELD_CLASS = 'flex w-full items-center gap-2 rounded-xl bg-white px-3 py-2 text-left text-[12.5px] '
    + 'font-medium text-slate-700 ring-1 ring-slate-200/70 shadow-[0_1px_2px_rgba(15,23,42,0.04)] transition-all '
    + 'hover:bg-slate-50 active:scale-[0.99] aria-expanded:ring-2 aria-expanded:ring-blue-500/60 [&>span]:flex-1';

export default function FrontOfficeTimesheetFilters({
    optionsState,
    value,
    onChange,
    layout = 'stack',
    period = null,
    onPeriodChange = null,
}) {
    const idBase = useId();
    const payload = optionsState?.payload || null;
    const status = optionsState?.status || 'idle';
    const options = useMemo(() => timesheetSelectOptions(payload), [payload]);
    // Пока грузится новый период, прежние списки остаются: выключать пикеры на
    // каждую смену дат — мигание ради мигания. Ждём только самый первый ответ.
    const ready = Boolean(payload);
    const loadingText = !payload && status === 'loading' ? 'Загрузка…' : null;
    const errorText = status === 'error' ? (optionsState?.error || 'Не удалось загрузить списки') : '';
    const current = value || {};
    // Только про тех, кто попадёт в файл при текущем отборе.
    const withoutCard = ready ? timesheetWithoutCard(payload, current) : [];
    // Пока строка сворачивается, в ней остаются прежние имена, а не пустой список.
    const shownWithoutCard = useRef([]);
    if (withoutCard.length) shownWithoutCard.current = withoutCard;
    const row = layout === 'row';

    const pickers = [
        {
            key: 'city',
            label: 'Город',
            node: (
                <CustomSelect
                    id={`${idBase}-city`}
                    variant="ios"
                    multiple
                    searchable
                    bulkActions
                    value={current.cities || []}
                    onChange={(cities) => onChange?.({ ...current, cities })}
                    options={options.cities}
                    placeholder={loadingText || 'Все города'}
                    renderValue={pickedLabel(options.cities, 'Все города')}
                    disabled={!ready}
                    ariaLabel="Город"
                    className={row ? 'w-[176px]' : ''}
                />
            ),
        },
        {
            key: 'person',
            label: 'Сотрудник',
            node: (
                <CustomSelect
                    id={`${idBase}-person`}
                    variant="ios"
                    multiple
                    searchable
                    bulkActions
                    value={(current.userIds || []).map(String)}
                    onChange={(userIds) => onChange?.({ ...current, userIds })}
                    options={options.people}
                    placeholder={loadingText || 'Все сотрудники'}
                    renderValue={pickedLabel(options.people, 'Все сотрудники')}
                    disabled={!ready}
                    ariaLabel="Сотрудник"
                    className={row ? 'w-[236px]' : ''}
                />
            ),
        },
        {
            key: 'job',
            label: 'Должность',
            node: (
                <CustomSelect
                    id={`${idBase}-job`}
                    variant="ios"
                    multiple
                    value={current.jobTitles || []}
                    onChange={(jobTitles) => onChange?.({ ...current, jobTitles })}
                    options={options.jobs}
                    placeholder={loadingText || 'Все должности'}
                    renderValue={pickedLabel(options.jobs, 'Все должности')}
                    disabled={!ready}
                    ariaLabel="Должность"
                    className={row ? 'w-[176px]' : ''}
                />
            ),
        },
    ];

    if (row) {
        return (
            <>
                {pickers.map((picker) => <React.Fragment key={picker.key}>{picker.node}</React.Fragment>)}
                {errorText && <span className="text-[12px] leading-snug text-rose-600">{errorText}</span>}
            </>
        );
    }

    return (
        <div className="flex flex-col gap-3">
            {period && onPeriodChange && (
                <div>
                    <span className="mb-1 block text-[12.5px] font-medium text-slate-600">Период</span>
                    <IosDateRangePicker
                        from={period.from}
                        to={period.to}
                        onChange={(next) => {
                            if (next?.from && next?.to) onPeriodChange(next);
                        }}
                        views={['days', 'months']}
                        presets={timesheetPeriodPresets()}
                        spanLimit={timesheetSpanLimit}
                        startHint={TIMESHEET_PERIOD_HINT}
                        formatLabel={(from, to) => timesheetPeriodFieldLabel({ from, to })}
                        triggerClassName={PERIOD_FIELD_CLASS}
                        portal
                    />
                </div>
            )}
            {pickers.map((picker) => (
                <PickerRow key={picker.key} id={`${idBase}-${picker.key}`} label={picker.label}>
                    {picker.node}
                </PickerRow>
            ))}
            {errorText && (
                <p className="text-[12.5px] leading-snug text-rose-600">{errorText}</p>
            )}
            <HoursFold open={withoutCard.length > 0}>
                <p className="text-[12px] leading-snug text-slate-500">
                    {`Нет карточки Workpace: ${shownWithoutCard.current.join(', ')}. Отметок по ним нет — дни без отметок останутся пустыми.`}
                </p>
            </HoursFold>
        </div>
    );
}
