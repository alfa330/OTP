import React, { useEffect, useRef, useState } from 'react';
import { Plus, X } from 'lucide-react';
import IosDatePicker from '../ui/DatePicker';
import { IosTimePicker } from '../ui/TimePicker';
import { INVALID_RING, T } from './disciplineUi';
import {
    TIMECODES_MAX, isOvernight, joinMoment, joinShift, joinTimecodes, shiftIsoDays, shiftParts, splitMoment,
    timecodeRows, todayIso,
} from './disciplineRules';

/*
 * Поля времени формы нарушения — пикеры портала вместо системных полей
 * (эталон — панель выгрузки табло СЗоВ): дата — календарём IosDatePicker,
 * время — полем IosTimePicker. В поле времени не набрать ничего, кроме
 * времени: «1430» само становится 14:30, ↑/↓ двигают на шаг (с Shift — на
 * час), панель часов и минут — по шеврону.
 *
 * Смена — две границы, «с» и «по», а не строка свободного текста: туда
 * попадали «рандомные числа» (владелец, 20.10.2026). Тайм-код записи экрана —
 * фрагменты «с … по …» тем же полем времени.
 *
 * Обёртка с красной рамкой стоит всегда, меняется только её класс: условная
 * обёртка пересоздала бы поле на первой же цифре и выбила бы из него фокус.
 */

// Поле времени ростом и кеглем — как остальные поля формы (dInput).
const TIME_INPUT = 'w-full rounded-xl border-0 bg-slate-100 px-7 py-2.5 text-center text-[14px] tabular-nums '
    + 'text-slate-900 placeholder-slate-400 transition focus:bg-white focus:outline-none focus:ring-2 '
    + 'focus:ring-blue-500/70 disabled:cursor-not-allowed disabled:opacity-40 2xl:py-3 2xl:text-[16px]';

// Кнопка календаря во всю ширину ячейки, шеврон — у правого края.
const DATE_TRIGGER = 'w-full [&>button]:w-full [&>button]:px-3.5 [&>button]:py-2.5 [&>button]:text-[14px] '
    + '[&>button]:font-normal [&>button]:text-slate-900 [&>button>span]:flex-1 [&>button>span]:text-left '
    + '2xl:[&>button]:px-4 2xl:[&>button]:py-3 2xl:[&>button]:text-[16px]';

const ring = (on) => `rounded-xl ${on ? INVALID_RING : ''}`;

const TimeBox = ({ value, onChange, invalid = false, step = 5, allowEmpty = true, defaultTime, ariaLabel, width = '' }) => (
    <div className={`${width || 'w-[112px] 2xl:w-[128px]'} shrink-0 ${ring(invalid)}`}>
        <IosTimePicker value={value} onChange={onChange} step={step} allowEmpty={allowEmpty} defaultTime={defaultTime}
                       ariaLabel={ariaLabel} className="w-full" inputClassName={TIME_INPUT} />
    </div>
);

/* Дата и время нарушения. Дата — не позже сегодня и не старше года (как
   проверяет сервер); время — с точностью до минуты. Будущее время поле не
   подменяет молча: форма скажет «ещё не наступило» у самого поля. */
export const MomentField = ({ value, onChange, invalid = false }) => {
    const { date, time } = splitMoment(value);
    const today = todayIso();
    return (
        <div className="flex items-center gap-2">
            <div className={`min-w-0 flex-1 ${ring(invalid)}`}>
                <IosDatePicker value={date} min={shiftIsoDays(today, -365)} max={today} ariaLabel="Дата нарушения"
                               className={DATE_TRIGGER} onChange={(next) => onChange(joinMoment(next, time))} />
            </div>
            <TimeBox value={time} step={1} allowEmpty={false} ariaLabel="Время нарушения" invalid={invalid}
                     onChange={(next) => onChange(joinMoment(date, next))} />
        </div>
    );
};

/* Смена «с — по». Конец раньше начала — ночная смена, она уходит в следующие
   сутки: под полями метка «+1 день», как в планировщике смен (в ряд с полями
   она не помещалась в половину формы). */
export const ShiftField = ({ value, onChange, invalid = false }) => {
    const { start, end, other } = shiftParts(value);
    return (
        <div className="space-y-1.5">
            <div className="flex items-center gap-2">
                <TimeBox value={start} defaultTime="09:00" ariaLabel="Начало смены" invalid={invalid && !start}
                         onChange={(next) => onChange(joinShift(next, end))} />
                <span className="text-slate-400" aria-hidden="true">–</span>
                <TimeBox value={end} defaultTime={start || '18:00'} ariaLabel="Конец смены"
                         invalid={invalid && (!end || end === start)}
                         onChange={(next) => onChange(joinShift(start, next))} />
            </div>
            {isOvernight(start, end) ? (
                <div className={`flex items-center gap-1.5 px-1 text-[12px] text-slate-500 ${T.note}`}>
                    <span className="shrink-0 rounded-full bg-orange-100 px-2 py-0.5 text-[11px] font-semibold text-orange-700">
                        +1 день
                    </span>
                    <span>конец смены — на следующий день</span>
                </div>
            ) : null}
            {other ? (
                <div className={`px-1 text-[12px] text-slate-500 ${T.note}`}>Было записано: «{other}» — укажите время смены</div>
            ) : null}
        </div>
    );
};

/* Смена из графика, когда в поле набрана другая: одно нажатие — и она снова в
   поле. Стоит под полем ПОСЛЕ его ошибки: красное «чего не хватает» — у самого поля. */
export const ScheduleShiftLink = ({ label, onPick }) => (
    <button type="button" onClick={onPick}
            className={`rounded-md px-1 text-left text-[12px] text-blue-600 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60 ${T.note}`}>
        В графике на это время — {label}
    </button>
);

let rowSeq = 0;
const withIds = (rows) => rows.map((row) => ({ ...row, id: (rowSeq += 1) }));
const emptyRow = () => withIds([{ start: '', end: '' }])[0];
const rowsOf = (note) => {
    const parsed = timecodeRows(note);
    return { rows: parsed.rows.length ? withIds(parsed.rows) : [emptyRow()], other: parsed.other };
};

/* Тайм-код записи экрана: фрагменты «с … по …» на записи в день нарушения;
   конец можно не указывать. Пустые строки в значение не попадают — пока
   фрагмент не набран, его нет и в черновике. */
export const TimecodeField = ({ value, onChange, invalid = false, defaultTime = '09:00' }) => {
    const [state, setState] = useState(() => rowsOf(value));
    const emitted = useRef(value || '');
    useEffect(() => {
        if ((value || '') === emitted.current) return;
        emitted.current = value || '';
        setState(rowsOf(value));
    }, [value]);

    const commit = (rows) => {
        const next = rows.length ? rows : [emptyRow()];
        setState({ rows: next, other: '' });
        const text = joinTimecodes(next);
        emitted.current = text;
        onChange(text);
    };
    const patch = (id, change) => commit(state.rows.map((row) => (row.id === id ? { ...row, ...change } : row)));
    const remove = (id) => commit(state.rows.filter((row) => row.id !== id));
    const last = state.rows[state.rows.length - 1];
    const canAdd = state.rows.length < TIMECODES_MAX && Boolean(last?.start);

    return (
        <div className="space-y-2">
            {state.rows.map((row, index) => {
                const removable = state.rows.length > 1 || row.start || row.end;
                return (
                    <div key={row.id} className="flex items-center gap-2">
                        <span className={`w-4 shrink-0 text-[13px] text-slate-500 ${T.sub}`}>с</span>
                        <TimeBox value={row.start} step={1} defaultTime={defaultTime}
                                 ariaLabel={`Тайм-код ${index + 1}: с`} invalid={invalid && !row.start}
                                 onChange={(next) => patch(row.id, { start: next })} />
                        <span className={`shrink-0 text-[13px] text-slate-500 ${T.sub}`}>по</span>
                        <TimeBox value={row.end} step={1} defaultTime={row.start || defaultTime}
                                 ariaLabel={`Тайм-код ${index + 1}: по`}
                                 onChange={(next) => patch(row.id, { end: next })} />
                        <button type="button" aria-label="Убрать фрагмент" onClick={() => remove(row.id)}
                                className={`grid h-7 w-7 shrink-0 place-items-center rounded-full text-slate-400 transition hover:bg-slate-200 hover:text-slate-600 ${removable ? '' : 'invisible'}`}>
                            <X size={14} />
                        </button>
                    </div>
                );
            })}
            {canAdd ? (
                <button type="button" onClick={() => setState((prev) => ({ ...prev, rows: [...prev.rows, emptyRow()] }))}
                        className={`inline-flex items-center gap-1 rounded-lg px-1 py-0.5 text-[12.5px] font-medium text-blue-600 transition hover:bg-blue-50 ${T.sub}`}>
                    <Plus size={13} />
                    Ещё фрагмент
                </button>
            ) : null}
            {state.other ? (
                <div className={`flex flex-wrap items-center gap-x-2 px-1 text-[12px] text-slate-500 ${T.note}`}>
                    <span>Было записано: «{state.other}»</span>
                    <button type="button" className="font-medium text-blue-600 hover:underline" onClick={() => commit([])}>
                        Убрать
                    </button>
                </div>
            ) : null}
        </div>
    );
};
