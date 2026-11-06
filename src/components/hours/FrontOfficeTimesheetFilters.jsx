import React, { useEffect, useId, useMemo, useRef, useState } from 'react';
import CustomSelect from '../ui/CustomSelect';
import HoursFold from './HoursFold';
import {
    pruneTimesheetFilters,
    sameTimesheetFilters,
    timesheetSelectOptions,
} from './frontOfficeTimesheet';
import { fetchTimesheetOptions } from './frontOfficeTimesheetApi';

/*
 * Отбор табеля фронт-офисов: город, сотрудник, должность (ТЗ, п. 6). Месяц —
 * тот, что выбран на странице. Пустой отбор — все: без отборов табель общий по
 * всем городам и сотрудникам, как требует ТЗ.
 *
 * Списки — состав выбранного месяца с сервера (/api/front_office_timesheet/options):
 * уволенный в этом месяце в нём есть, он и в табеле есть. Сменили месяц — из
 * отбора отпадает то, чего в новом месяце нет.
 *
 * Пикеры — общий CustomSelect в виде iOS (эталон пикеров портала), выбор
 * нескольких значений с поиском.
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

export default function FrontOfficeTimesheetFilters({ month, apiBaseUrl, authHeaders, value, onChange }) {
    const [state, setState] = useState({ status: 'loading', error: '', payload: null });
    const latest = useRef({ value, onChange, authHeaders });
    latest.current = { value, onChange, authHeaders };
    const idBase = useId();

    useEffect(() => {
        if (!month) return undefined;
        const controller = new AbortController();
        setState((previous) => ({ ...previous, status: 'loading', error: '' }));
        fetchTimesheetOptions({
            apiBaseUrl,
            headers: latest.current.authHeaders?.() || {},
            month,
            signal: controller.signal,
        })
            .then((payload) => {
                setState({ status: 'ready', error: '', payload });
                const pruned = pruneTimesheetFilters(latest.current.value, timesheetSelectOptions(payload));
                if (!sameTimesheetFilters(pruned, latest.current.value)) latest.current.onChange?.(pruned);
            })
            .catch((error) => {
                if (error?.name === 'AbortError') return;
                setState({ status: 'error', error: error?.message || 'Не удалось загрузить списки', payload: null });
            });
        return () => controller.abort();
    }, [month, apiBaseUrl]);

    const options = useMemo(() => timesheetSelectOptions(state.payload), [state.payload]);
    const ready = state.status === 'ready';
    const loadingText = state.status === 'loading' ? 'Загрузка…' : null;
    const withoutCard = ready ? (state.payload?.without_card || []) : [];
    // Пока строка сворачивается, в ней остаются прежние имена, а не пустой список.
    const shownWithoutCard = useRef([]);
    if (withoutCard.length) shownWithoutCard.current = withoutCard;
    const current = value || {};

    return (
        <div className="flex flex-col gap-3">
            <PickerRow id={`${idBase}-city`} label="Город">
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
                />
            </PickerRow>
            <PickerRow id={`${idBase}-person`} label="Сотрудник">
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
                />
            </PickerRow>
            <PickerRow id={`${idBase}-job`} label="Должность">
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
                />
            </PickerRow>
            {state.status === 'error' && (
                <p className="text-[12.5px] leading-snug text-rose-600">{state.error}</p>
            )}
            <HoursFold open={withoutCard.length > 0}>
                <p className="text-[12px] leading-snug text-slate-500">
                    {`Нет карточки Workpace: ${shownWithoutCard.current.join(', ')}. Отметок по ним нет — дни без отметок останутся пустыми.`}
                </p>
            </HoursFold>
        </div>
    );
}
