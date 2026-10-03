import React, { useId } from 'react';
import { ChevronLeft, ChevronRight } from 'lucide-react';
import { iosBtnGhost, iosCard, scoreTone } from '../ui/ios';
import { SCORE_TEXT } from './QueueList';
import { dayTitle, monthGroups } from './queueDayRules';

/* Общие кирпичи экранов «по дням» раздела ИИ-оценки.
 *
 * Формат владелец утвердил на «Очереди ревью» (30.09.2026, третья версия): дни
 * по месяцам, у месяца один список со строками через волосяную линию, по
 * нажатию — экран дня. 02.10.2026 он велел перевести на тот же формат «Звонки»
 * и «Чаты», а сводка ИИ по дням встала рядом. Четыре экрана с одной вёрсткой —
 * это одна вёрстка, а не четыре копии: листок календаря, балл, клетка сводки,
 * шапка дня со стрелками и месяц живут здесь, и разъехаться не могут.
 */

const WEEKDAY_SHORT = ['вс', 'пн', 'вт', 'ср', 'чт', 'пт', 'сб'];

/* Листок календаря: день недели и число, как значок «Календаря» — месяц уже в
 * заголовке раздела. По числам глаз идёт по ленте дней; сегодняшний — синий. */
export function DateLeaf({ day, info }) {
    const parts = String(day.day).split('-');
    const date = parts.length === 3 ? Number(parts[2]) : null;
    const today = info.relative === 'Сегодня';
    return (
        <div aria-hidden="true"
             className={`flex h-11 w-11 shrink-0 flex-col items-center justify-center rounded-[10px] ${
                 today ? 'bg-blue-500 text-white' : 'bg-slate-100 text-slate-900'}`}>
            {date == null ? (
                <span className="text-[17px] font-semibold text-slate-400">—</span>
            ) : (
                <>
                    <span className={`text-[10px] font-semibold uppercase leading-none tracking-wide ${
                        today ? 'text-white/85' : 'text-slate-500'}`}>{WEEKDAY_SHORT[new Date(`${day.day}T12:00:00`).getDay()]}</span>
                    <span className="mt-[3px] text-[18px] font-semibold leading-none tabular-nums">{date}</span>
                </>
            )}
        </div>
    );
}

/* Балл числом в цвет балла; нет балла — прочерк. */
export function Score({ value, className = '' }) {
    return (
        <span className={`font-semibold tabular-nums ${value == null ? 'text-slate-300' : SCORE_TEXT[scoreTone(value)]} ${className}`}>
            {value ?? '—'}
        </span>
    );
}

/* Показатель сводки дня: подпись, крупное число, пояснение. Клетки разделены
 * волосяными линиями одной сеткой (gap-px на сером), а не рамками у каждой. */
export function DayStat({ label, value, tone = null, sub = null, subTone = 'text-slate-500', wide = false, children = null }) {
    return (
        <div className={`min-w-0 bg-white px-4 py-3.5 sm:px-5 ${wide ? 'col-span-2 sm:col-span-1' : ''}`}>
            <div className="truncate text-[12px] text-slate-500">{label}</div>
            <div className={`mt-1 text-[24px] font-semibold leading-none tabular-nums ${
                value == null ? 'text-slate-300' : tone ? SCORE_TEXT[tone] : 'text-slate-900'}`}>
                {value ?? '—'}
            </div>
            {children}
            {sub && <div className={`mt-1.5 truncate text-[12px] ${subTone}`}>{sub}</div>}
        </div>
    );
}

/* Полоса «проверено N из M» под числом — одна на все экраны. */
export function ShareBar({ share }) {
    return (
        <div className="mt-2.5 h-1 overflow-hidden rounded-full bg-slate-100" aria-hidden="true">
            <div className="h-full rounded-full bg-emerald-500 transition-[width] duration-500"
                 style={{ width: `${Math.round(Math.min(1, Math.max(0, share || 0)) * 100)}%` }} />
        </div>
    );
}

const ARROW = 'grid h-8 w-8 place-items-center rounded-lg text-slate-600 transition hover:bg-white hover:shadow-sm disabled:cursor-default disabled:text-slate-300 disabled:hover:bg-transparent disabled:hover:shadow-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60';

/* Шапка экрана дня: «‹ Все дни», стрелки к соседним дням и сама дата.
 * `titleId` — заголовок, куда CallQaView ставит фокус после смены экрана;
 * у каждой вкладки свой, иначе две вкладки делили бы один id. */
export function DayHeader({ day, earlier, later, onClose, onOpenDay, titleId, right = null }) {
    const info = dayTitle(day);
    return (
        <>
            <div className="flex items-center justify-between gap-2">
                <button type="button" onClick={onClose} className={`${iosBtnGhost} -ml-2`}>
                    <ChevronLeft size={16} aria-hidden="true" />Все дни
                </button>
                <div className="flex items-center gap-0.5 rounded-xl bg-slate-100 p-0.5">
                    <button type="button" className={ARROW} disabled={!earlier} onClick={() => onOpenDay(earlier)}
                            aria-label={earlier ? `Предыдущий день: ${dayTitle(earlier).title}` : 'Предыдущего дня нет'}
                            title={earlier ? dayTitle(earlier).title : undefined}>
                        <ChevronLeft size={16} aria-hidden="true" />
                    </button>
                    <button type="button" className={ARROW} disabled={!later} onClick={() => onOpenDay(later)}
                            aria-label={later ? `Следующий день: ${dayTitle(later).title}` : 'Следующего дня нет'}
                            title={later ? dayTitle(later).title : undefined}>
                        <ChevronRight size={16} aria-hidden="true" />
                    </button>
                </div>
            </div>
            <div className="flex flex-wrap items-end justify-between gap-x-4 gap-y-2 px-1">
                <div className="min-w-0">
                    <div className={`text-[13px] font-medium ${info.relative ? 'text-blue-600' : 'text-slate-500'}`}>
                        {info.relative ? `${info.relative}, ${info.weekday.toLowerCase()}` : info.weekday || ' '}
                    </div>
                    <h2 id={titleId} tabIndex={-1}
                        className="rounded-md text-[24px] font-semibold leading-tight text-slate-900 outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60">
                        {info.date}
                    </h2>
                </div>
                {right}
            </div>
        </>
    );
}

/* Месяц — заголовок, итог справа и один список его дней. `columns` — сетка
 * колонок на компьютере (общая у шапки и строк, иначе они разъедутся), `head` —
 * подписи колонок, `renderDay` — строка дня. */
export function MonthSection({ group, columns, head, aside = null, renderDay }) {
    const headingId = useId();
    return (
        <section aria-labelledby={headingId} className="space-y-2">
            <div className="flex items-baseline justify-between gap-3 px-1">
                <h2 id={headingId} className="text-[18px] font-semibold text-slate-900">{group.title}</h2>
                {aside}
            </div>
            <div className={`${iosCard} overflow-hidden`}>
                <div aria-hidden="true"
                     className={`hidden border-b border-slate-100 px-5 py-2 text-[11px] font-medium uppercase tracking-wide text-slate-400 ${columns}`}>
                    {head}
                </div>
                <div role="list">
                    {group.days.map((day) => (
                        <div role="listitem" key={day.day} className="border-b border-slate-100 last:border-b-0">
                            {renderDay(day)}
                        </div>
                    ))}
                </div>
            </div>
        </section>
    );
}

/* Классы строки дня и её шеврона — одни на все списки дней. */
export const DAY_ROW_CLASS = 'group flex w-full items-center gap-3 px-4 py-3 text-left transition hover:bg-slate-50 focus-visible:bg-blue-50/60 focus-visible:outline-none active:bg-slate-100 sm:px-5';

export function RowChevron() {
    return (
        <ChevronRight size={16} aria-hidden="true"
                      className="shrink-0 text-slate-300 transition group-hover:translate-x-0.5 group-hover:text-slate-400" />
    );
}

export { monthGroups, dayTitle };
