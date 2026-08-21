import React, { useMemo, useState } from 'react';
import { IosModal, iosCard, iosBtnSecondary } from '../ui/ios';
import SessionList from './SessionList';
import {
    durationMinutes, formatDayLong, formatDuration, pluralSessions, pluralPeople, todayIso,
} from './constants';
import useEscapeClose from './useEscapeClose';

/* Календарь месяца.
 *
 * Что изменилось против прошлой версии. Раньше день был квадратом с точками по
 * супервайзерам, а цвет точки выдавался «золотым углом» на каждый рендер —
 * то есть при любом обновлении цвета перетасовывались, и запомнить их было
 * нельзя. Смысла в этом цвете не было никакого, поэтому его здесь нет вовсе:
 * день несёт ОДНО число — сколько занятий, — а насыщенность фона показывает,
 * много их или мало. Всё остальное открывается по клику.
 *
 * Модалка дня раньше была двухпанельной: список слева, выбранное занятие
 * справа. Второй панели больше нет — занятие и так целиком видно в строке,
 * а «выбери слева, читай справа» на пяти строках это лишний шаг.
 */

const WEEKDAYS = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];

/* Высота клетки фиксированная, а не aspect-square. Квадрат на широком экране
 * растягивает клетку до 160 px, и месяц занимает два экрана ради тридцати
 * однозначных чисел — ровно то пустое место, из-за которого сетку хочется
 * пролистать, а не прочитать. */
const CELL_HEIGHT = 'h-[62px] sm:h-[72px]';

export default function TrainingsCalendar({ month, sessions = [], canManage, onEdit, onDelete }) {
    const [openDay, setOpenDay] = useState(null);

    useEscapeClose(Boolean(openDay), () => setOpenDay(null));

    const [year, monthNumber] = useMemo(
        () => String(month || '').split('-').map(Number),
        [month],
    );

    const byDay = useMemo(() => {
        const map = new Map();
        sessions.forEach((session) => {
            const day = Number(String(session?.date || '').slice(8, 10));
            if (!Number.isFinite(day)) return;
            if (!map.has(day)) map.set(day, []);
            map.get(day).push(session);
        });
        return map;
    }, [sessions]);

    const busiest = useMemo(
        () => Array.from(byDay.values()).reduce((acc, items) => Math.max(acc, items.length), 0),
        [byDay],
    );

    const cells = useMemo(() => {
        if (!Number.isFinite(year) || !Number.isFinite(monthNumber)) return [];
        const first = new Date(year, monthNumber - 1, 1);
        // getDay(): 0 — воскресенье. Неделя в календаре начинается с понедельника.
        const lead = (first.getDay() + 6) % 7;
        const daysInMonth = new Date(year, monthNumber, 0).getDate();
        const result = [];
        for (let i = 0; i < lead; i += 1) result.push(null);
        for (let day = 1; day <= daysInMonth; day += 1) result.push(day);
        while (result.length % 7 !== 0) result.push(null);
        return result;
    }, [year, monthNumber]);

    const today = todayIso();

    const openDaySessions = useMemo(() => (
        openDay ? (byDay.get(openDay) || []) : []
    ), [openDay, byDay]);

    const openDayIso = openDay
        ? `${year}-${String(monthNumber).padStart(2, '0')}-${String(openDay).padStart(2, '0')}`
        : '';

    const dayStats = useMemo(() => {
        const people = new Set(openDaySessions.map((item) => item.operator_id));
        const minutes = openDaySessions.reduce((acc, item) => acc + durationMinutes(item), 0);
        return { people: people.size, minutes };
    }, [openDaySessions]);

    return (
        <>
            <div className={`${iosCard} p-3 sm:p-4`}>
                <div className="mb-2 grid grid-cols-7 gap-1.5">
                    {WEEKDAYS.map((label) => (
                        <div key={label} className="px-1 text-center text-[11px] font-semibold uppercase tracking-wider text-slate-400">
                            {label}
                        </div>
                    ))}
                </div>

                <div className="grid grid-cols-7 gap-1.5">
                    {cells.map((day, index) => {
                        if (day == null) return <div key={`pad-${index}`} className={CELL_HEIGHT} />;

                        const items = byDay.get(day) || [];
                        const count = items.length;
                        const iso = `${year}-${String(monthNumber).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
                        const isToday = iso === today;
                        // Насыщенность вместо разных цветов: один смысл — «сколько»,
                        // значит одна шкала, а не палитра.
                        const weight = count === 0 ? 0 : Math.min(4, Math.ceil((count / Math.max(1, busiest)) * 4));
                        const tone = [
                            'bg-slate-50 text-slate-400',
                            'bg-blue-50/70 text-blue-700 ring-1 ring-blue-100',
                            'bg-blue-100/80 text-blue-800 ring-1 ring-blue-200/70',
                            'bg-blue-200/70 text-blue-900 ring-1 ring-blue-300/60',
                            'bg-blue-300/70 text-blue-950 ring-1 ring-blue-400/50',
                        ][weight];

                        return (
                            <button
                                key={iso}
                                type="button"
                                disabled={count === 0}
                                onClick={() => setOpenDay(day)}
                                title={count > 0
                                    ? `${formatDayLong(iso)}: ${count} ${pluralSessions(count)}`
                                    : formatDayLong(iso)}
                                className={`relative flex ${CELL_HEIGHT} flex-col items-center justify-center gap-0.5 rounded-xl transition-all ${tone} ${
                                    count > 0 ? 'cursor-pointer hover:brightness-[0.97] active:scale-[0.97]' : 'cursor-default'
                                } ${isToday ? 'ring-2 ring-slate-900/70' : ''}`}
                            >
                                <span className="text-[12px] font-medium tabular-nums leading-none opacity-70">
                                    {day}
                                </span>
                                {count > 0 && (
                                    <span className="text-[15px] font-semibold tabular-nums leading-none">
                                        {count}
                                    </span>
                                )}
                            </button>
                        );
                    })}
                </div>

                <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-slate-100 pt-3 text-[11.5px] text-slate-400">
                    <span>Число в клетке — сколько занятий провели в этот день</span>
                    {busiest > 0 && (
                        <>
                            <span className="text-slate-300">·</span>
                            <span className="tabular-nums">максимум за день: {busiest}</span>
                        </>
                    )}
                </div>
            </div>

            <IosModal
                open={Boolean(openDay)}
                onClose={() => setOpenDay(null)}
                title={openDayIso ? formatDayLong(openDayIso) : ''}
                subtitle={openDaySessions.length > 0
                    ? `${openDaySessions.length} ${pluralSessions(openDaySessions.length)} · ${dayStats.people} ${pluralPeople(dayStats.people)} · ${formatDuration(dayStats.minutes)}`
                    : undefined}
                maxWidth="max-w-xl"
                footer={(
                    <button type="button" onClick={() => setOpenDay(null)} className={iosBtnSecondary}>
                        Закрыть
                    </button>
                )}
            >
                <SessionList
                    sessions={openDaySessions}
                    showPerson
                    showTopic
                    canManage={canManage}
                    onEdit={(session) => { setOpenDay(null); onEdit?.(session); }}
                    onDelete={onDelete}
                    emptyText="В этот день занятий не было"
                />
            </IosModal>
        </>
    );
}
