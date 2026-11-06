import React, { useEffect, useMemo, useRef, useState } from 'react';
import InfoHint from '../common/InfoHint';
import { TIMESHEET_LEGEND, timesheetCellTitle, timesheetCellView, timesheetColumnTotals } from './frontOfficeTimesheet';
import { fetchTimesheetData } from './frontOfficeTimesheetApi';

/*
 * Табель фронт-офисов на экране «Учета часов» — в стилистике раздела, а не файла
 * (просьба владельца 06.11.2026). Данные те же, что в Excel (/api/front_office_timesheet/data):
 * города, люди, статус каждого дня, минуты опоздания и раннего ухода, итоги.
 *
 * Разметка повторяет таблицу часов: та же прокрутка внутри карточки, липкая
 * шапка дней (число и день недели, сегодня синим) и липкий столбец людей,
 * заголовок группы — строкой с числом, у уволенных — тот же бейдж «Уволен».
 * Рабочий день нейтральный — его большинство, красить нечего; выходной
 * приглушён; отсутствия — мягкими плашками; опоздание — красным числом, ранний
 * уход — оранжевым. Расшифровка — под «i» в шапке, а не строкой над таблицей.
 */
const NAME_COL = 'w-56 shrink-0 px-3 py-2 border-r border-slate-100';
const SMALL_COL = 'w-16 shrink-0 px-1 py-2 text-center border-r border-slate-100';
const DAY_COL = 'w-11 shrink-0 px-0.5 py-1.5 text-center border-l border-slate-100';
const TOTAL_COL = 'w-[92px] shrink-0 px-2 py-2 text-center border-l border-slate-100';

const TONE_CLASS = {
    work: 'text-[12.5px] font-medium text-slate-700',
    off: 'text-[12px] text-slate-300',
    bs: 'rounded-md bg-amber-50 px-1 text-[10.5px] font-semibold text-amber-700 ring-1 ring-amber-100',
    vacation: 'rounded-md bg-emerald-50 px-1 text-[10.5px] font-semibold text-emerald-700 ring-1 ring-emerald-100',
    sick: 'rounded-md bg-sky-50 px-1 text-[10.5px] font-semibold text-sky-700 ring-1 ring-sky-100',
    absent: 'rounded-md bg-rose-50 px-1 text-[10.5px] font-semibold text-rose-600 ring-1 ring-rose-100',
    empty: '',
};

const TOTAL_HEADERS = [
    { key: 'worked', label: 'Отработано', hint: 'дней' },
    { key: 'missed', label: 'Пропущено', hint: 'Б/С и н' },
    { key: 'late', label: 'Опоздания', hint: 'мин' },
    { key: 'early', label: 'Ранний уход', hint: 'мин' },
];

const todayIso = () => {
    const now = new Date();
    return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
};

function totalTone(key, value) {
    if (!value) return 'text-slate-300';
    if (key === 'worked') return 'font-semibold text-slate-800';
    if (key === 'early') return 'font-semibold text-amber-600';
    return 'font-semibold text-rose-500';
}

function DayCell({ day, cell }) {
    const view = timesheetCellView(cell);
    return (
        <div className={`${DAY_COL} relative flex flex-col items-center justify-center gap-0.5 leading-none`} title={timesheetCellTitle(day, cell)}>
            {view.incomplete && (
                <span aria-hidden="true" className="absolute right-1 top-1 h-1.5 w-1.5 rounded-full bg-amber-400" />
            )}
            <span className={`${TONE_CLASS[view.tone]} leading-[16px]`}>{view.label || ' '}</span>
            {(view.late > 0 || view.early > 0) ? (
                <span className="flex items-center gap-0.5 text-[10px] font-semibold tabular-nums">
                    {view.late > 0 && <span className="text-rose-500">{view.late}</span>}
                    {view.early > 0 && <span className="text-amber-600">{view.early}</span>}
                </span>
            ) : (
                <span className="text-[10px]">{' '}</span>
            )}
        </div>
    );
}

export default function FrontOfficeTimesheetView({ month, apiBaseUrl, authHeaders, filters = null, reloadKey = 0 }) {
    const [state, setState] = useState({ status: 'loading', error: '', payload: null });
    const latest = useRef({ authHeaders, filters });
    latest.current = { authHeaders, filters };
    const filtersKey = JSON.stringify(filters || {});

    useEffect(() => {
        if (!month) return undefined;
        const controller = new AbortController();
        setState((previous) => ({ ...previous, status: 'loading', error: '' }));
        fetchTimesheetData({
            apiBaseUrl,
            headers: latest.current.authHeaders?.() || {},
            month,
            filters: latest.current.filters,
            signal: controller.signal,
        })
            .then((payload) => setState({ status: 'ready', error: '', payload }))
            .catch((error) => {
                if (error?.name === 'AbortError') return;
                setState({ status: 'error', error: error?.message || 'Не удалось загрузить табель', payload: null });
            });
        return () => controller.abort();
    }, [month, apiBaseUrl, filtersKey, reloadKey]);

    const payload = state.payload;
    const days = useMemo(() => (Array.isArray(payload?.days) ? payload.days : []), [payload]);
    const totals = useMemo(() => timesheetColumnTotals(payload), [payload]);
    const today = todayIso();
    const withoutCard = Array.isArray(payload?.without_card) ? payload.without_card : [];

    let body;
    if (state.status === 'loading' && !payload) {
        body = <div className="px-4 py-12 text-[13px] text-slate-400">Загрузка табеля…</div>;
    } else if (state.status === 'error') {
        body = <div className="px-4 py-12 text-[13px] text-rose-600">{state.error}</div>;
    } else if (!payload?.cities?.length) {
        body = <div className="px-4 py-12 text-[13px] text-slate-400">Сотрудников за этот месяц нет.</div>;
    } else {
        body = (
            <>
                {payload.cities.map((city) => (
                    <div key={`city-${city.city}`} className="w-full">
                        <div className="w-full border-b border-slate-100 bg-slate-50/70 py-1.5 text-[12px] font-semibold text-slate-600">
                            <span className="sticky left-0 inline-flex items-center gap-1.5 px-3">
                                {city.city}
                                <span className="font-normal text-slate-400">{city.people.length}</span>
                            </span>
                        </div>
                        {city.people.map((person) => (
                            <div key={person.user_id} className="group relative flex w-max items-center border-b border-slate-100 transition-colors hover:bg-slate-50">
                                <div className={`${NAME_COL} sticky left-0 z-20 bg-white transition-colors group-hover:bg-slate-50`}>
                                    <div className="flex min-w-0 items-center gap-1.5">
                                        <span className="truncate text-[13px] font-medium text-slate-800">{person.name}</span>
                                        {person.active === false && (
                                            <span className="shrink-0 rounded-full bg-rose-50 px-1.5 py-0.5 text-[10px] font-semibold leading-none text-rose-600 ring-1 ring-rose-100">
                                                Уволен
                                            </span>
                                        )}
                                    </div>
                                    <div className="truncate text-[11.5px] text-slate-400">{person.job_title}</div>
                                </div>
                                <div className={`${SMALL_COL} text-[12.5px] text-slate-600`}>{person.schedule || '—'}</div>
                                <div className={`${SMALL_COL} text-[12.5px] text-slate-600`}>{person.plan}</div>
                                {days.map((day, index) => (
                                    <DayCell key={day.date} day={day} cell={person.cells?.[index]} />
                                ))}
                                {TOTAL_HEADERS.map((column) => (
                                    <div key={column.key} className={`${TOTAL_COL} text-[13px] tabular-nums ${totalTone(column.key, person.totals?.[column.key])}`}>
                                        {Number(person.totals?.[column.key] || 0)}
                                    </div>
                                ))}
                            </div>
                        ))}
                    </div>
                ))}
                <div className="sticky bottom-0 z-30 flex w-max border-t border-slate-200/80 bg-slate-50 text-[12px] font-medium text-slate-500">
                    <div className={`${NAME_COL} sticky left-0 z-40 bg-slate-50 text-slate-700`}>
                        Итого <span className="font-normal text-slate-400">· {totals.people}</span>
                    </div>
                    <div className={SMALL_COL} />
                    <div className={SMALL_COL} />
                    {days.map((day, index) => (
                        <div key={day.date} className={`${DAY_COL} text-[11px] tabular-nums text-slate-500`} title="Отработали в этот день">
                            {totals.perDay[index] || ''}
                        </div>
                    ))}
                    {TOTAL_HEADERS.map((column) => (
                        <div key={column.key} className={`${TOTAL_COL} text-[13px] tabular-nums ${totalTone(column.key, totals[column.key])}`}>
                            {totals[column.key]}
                        </div>
                    ))}
                </div>
            </>
        );
    }

    return (
        <>
            {withoutCard.length > 0 && (
                <div className="mx-5 mb-3 text-[12px] leading-snug text-slate-500">
                    {`Нет карточки Workpace: ${withoutCard.join(', ')} — дни без отметок у них пустые.`}
                </div>
            )}
            <div className="hours-scroll hours-table-scroll isolate min-h-[240px] overflow-auto rounded-b-2xl border-t border-slate-200/70">
                <div className="min-w-max tabular-nums">
                    <div className="sticky top-0 z-30 flex w-max border-b border-slate-200/80 bg-slate-50 text-[12px] font-medium text-slate-500">
                        <div className={`${NAME_COL} sticky left-0 z-40 flex items-center gap-1.5 bg-slate-50`}>
                            Сотрудник
                            <InfoHint title="Табель" side="right">
                                <div className="flex flex-col gap-1.5">
                                    {TIMESHEET_LEGEND.map((line) => <p key={line}>{line}</p>)}
                                </div>
                            </InfoHint>
                        </div>
                        <div className={`${SMALL_COL} flex items-center justify-center bg-slate-50`}>График</div>
                        <div className={`${SMALL_COL} flex items-center justify-center bg-slate-50`}>План</div>
                        {days.map((day) => {
                            const isToday = day.date === today;
                            const marked = day.weekend || day.holiday;
                            return (
                                <div
                                    key={day.date}
                                    className={`${DAY_COL} flex flex-col items-center justify-center gap-1 leading-none ${marked ? 'bg-slate-100/80' : ''}`}
                                    title={day.holiday ? 'Праздник' : undefined}
                                >
                                    <span className={`text-[13px] font-semibold ${isToday ? 'text-blue-600' : 'text-slate-700'}`}>{day.day}</span>
                                    <span className={`text-[10px] font-medium ${isToday ? 'text-blue-500' : 'text-slate-400'}`}>{day.weekday}</span>
                                </div>
                            );
                        })}
                        {TOTAL_HEADERS.map((column) => (
                            <div key={column.key} className={`${TOTAL_COL} flex flex-col items-center justify-center gap-0.5 bg-slate-50 leading-tight`}>
                                <span>{column.label}</span>
                                <span className="text-[10.5px] font-normal text-slate-400">{column.hint}</span>
                            </div>
                        ))}
                    </div>
                    {body}
                </div>
            </div>
        </>
    );
}
