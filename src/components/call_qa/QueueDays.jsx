import React, { useId } from 'react';
import { CheckCircle2, ChevronLeft, ChevronRight, Loader2, RotateCcw } from 'lucide-react';
import { iosBtnGhost, iosBtnPrimary, iosBtnSecondary, iosCard, iosGroupLabel, IosHint, scoreTone } from '../ui/ios';
import QueueList, { QuietMark, REASON, SCORE_TEXT, STALE } from './QueueList';
import {
    commonReasons, commonRowMeta, dayNeighbours, dayTitle, itemKey, monthGroups, nextDayToReview,
    plural, queueSummary, STALE_MARK,
} from './queueDayRules';

/* Очередь ревью по дням разговора — два экрана, как «Почта» или «Календарь» iOS.
 *
 * Сначала — дни, сгруппированные по месяцам. У месяца один список: строки дней
 * разделены волосяной линией, а не разнесены отдельными карточками — так лента
 * читается сверху вниз одним взглядом. Строка — сводка дня: листок календаря,
 * сколько ждёт проверки и сколько из них критических, кто работал; справа
 * колонками средние баллы ИИ и человека и сколько уже проверено. Подписи колонок —
 * один раз в шапке списка, а не в каждой строке. Разговоров в строке нет: по
 * нажатию открывается экран дня — полная аналитика дня и список его разговоров.
 * Со стрелками можно перейти к соседнему дню, не возвращаясь к списку, а проверив
 * весь день — сразу к следующему, где ещё есть работа.
 *
 * Данные и состояние (какой день открыт, загруженные строки, только что
 * проверенные) живут в CallQaView: из карточки разговора человек возвращается на
 * тот же экран дня.
 */

const criticalText = (n) => `${n} ${plural(n, 'критическое', 'критических', 'критических')}`;
const waitingText = (n) => plural(n, 'ждёт проверки', 'ждут проверки', 'ждут проверки');
const round = (value) => (value == null ? null : Math.round(value));

// Колонки списка дней на компьютере — одни на шапку и строки, иначе разъедутся.
const DAY_COLUMNS = 'sm:grid sm:grid-cols-[minmax(0,1fr)_4rem_4.5rem_5.5rem_1rem] sm:items-center sm:gap-4';

const WEEKDAY_SHORT = ['вс', 'пн', 'вт', 'ср', 'чт', 'пт', 'сб'];

/* Листок календаря: день недели и число, как значок «Календаря» — месяц уже в
 * заголовке раздела. По числам глаз идёт по ленте дней; сегодняшний — синий. */
function DateLeaf({ day, info }) {
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
function Score({ value, className = '' }) {
    return (
        <span className={`font-semibold tabular-nums ${value == null ? 'text-slate-300' : SCORE_TEXT[scoreTone(value)]} ${className}`}>
            {value ?? '—'}
        </span>
    );
}

/* Строка дня — кнопка на весь день. Главное — сколько ждёт проверки; критические —
 * красным (единственный красный в строке), рядом — кто работал. На компьютере
 * баллы и «проверено» — в колонках справа; на телефоне — третьей строкой. */
function DayRow({ day, onOpen }) {
    const info = dayTitle(day.day);
    const done = day.open === 0;
    const evaluated = Math.max(day.evaluated || 0, day.open || 0);
    const ai = round(day.ai_avg);
    const human = round(day.human_avg);
    // Кто работал в этот день — то, чего в строке больше нигде нет.
    const directions = (day.directions || []).map((d) => d.name);
    const who = [
        day.operators ? `${day.operators} ${plural(day.operators, 'сотрудник', 'сотрудника', 'сотрудников')}` : null,
        directions.length ? directions.slice(0, 2).join(', ') + (directions.length > 2 ? ` +${directions.length - 2}` : '') : null,
    ].filter(Boolean).join(' · ');
    const label = [info.title, done ? 'всё проверено' : `${day.open} ${waitingText(day.open)}`,
        day.critical ? criticalText(day.critical) : null].filter(Boolean).join(', ');
    return (
        <button type="button" onClick={() => onOpen(day.day)} data-qa-day-tile={day.day} aria-label={label}
                className={`group flex w-full items-center gap-3 px-4 py-3 text-left transition hover:bg-slate-50 focus-visible:bg-blue-50/60 focus-visible:outline-none active:bg-slate-100 sm:px-5 ${DAY_COLUMNS}`}>
            <div className="flex min-w-0 flex-1 items-center gap-3.5">
                <DateLeaf day={day} info={info} />
                <div className="min-w-0">
                    {done ? (
                        <div className="flex items-center gap-1.5 text-[14px] font-semibold text-emerald-600">
                            <CheckCircle2 size={15} aria-hidden="true" />Всё проверено
                        </div>
                    ) : (
                        <div className="flex items-baseline gap-1.5">
                            <span className="text-[16px] font-semibold tabular-nums text-slate-900">{day.open}</span>
                            <span className="text-[13.5px] text-slate-700">{waitingText(day.open)}</span>
                        </div>
                    )}
                    <div className="mt-0.5 truncate text-[12.5px] text-slate-500">
                        {!done && day.critical > 0 && (
                            <span className="font-medium text-rose-600">{criticalText(day.critical)}{who ? ' · ' : ''}</span>
                        )}
                        {who}
                    </div>
                    <div className="mt-1 flex items-baseline gap-3 text-[12px] text-slate-400 sm:hidden">
                        <span>ИИ <Score value={ai} /></span>
                        <span>Человек <Score value={human} /></span>
                        <span>проверено <span className="font-medium tabular-nums text-slate-600">{day.reviewed || 0} из {evaluated}</span></span>
                    </div>
                </div>
            </div>
            <Score value={ai} className="hidden text-right text-[15px] sm:block" />
            <Score value={human} className="hidden text-right text-[15px] sm:block" />
            <span className="hidden text-right text-[13px] tabular-nums text-slate-600 sm:block">
                {day.reviewed || 0} из {evaluated}
            </span>
            <ChevronRight size={16} aria-hidden="true"
                          className="shrink-0 text-slate-300 transition group-hover:translate-x-0.5 group-hover:text-slate-400" />
        </button>
    );
}

function Overview({ days, total, truncated, onOpenDay, onRefresh }) {
    const summary = queueSummary(days);
    const groups = monthGroups(days);
    return (
        <div className="space-y-6">
            <div className="flex items-center justify-between gap-3 px-1">
                <p className="text-[13px] text-slate-500">
                    {summary.open ? (
                        <>
                            <b className="font-semibold text-slate-900">{summary.open}</b>
                            {' '}{plural(summary.open, 'разговор ждёт', 'разговора ждут', 'разговоров ждут')} проверки
                            {summary.critical > 0 && (
                                <span className="font-medium text-rose-600">{' · '}{criticalText(summary.critical)}</span>
                            )}
                        </>
                    ) : 'Все разговоры в очереди проверены'}
                    {truncated && <span> · показаны первые {total}</span>}
                </p>
                <button type="button" onClick={onRefresh} title="Обновить очередь" aria-label="Обновить очередь"
                        className="grid h-9 w-9 shrink-0 place-items-center rounded-xl text-slate-400 transition hover:bg-slate-200/60 hover:text-slate-600 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60">
                    <RotateCcw size={16} />
                </button>
            </div>
            {groups.map((group) => (
                <MonthSection key={group.key} group={group} onOpenDay={onOpenDay} />
            ))}
        </div>
    );
}

/* Месяц — заголовок и один список его дней. */
function MonthSection({ group, onOpenDay }) {
    const headingId = useId();
    return (
        <section aria-labelledby={headingId} className="space-y-2">
            <div className="flex items-baseline justify-between gap-3 px-1">
                <h2 id={headingId} className="text-[18px] font-semibold text-slate-900">{group.title}</h2>
                {group.open > 0 && (
                    <span className="text-[12.5px] text-slate-500">
                        {group.open} {waitingText(group.open)}
                        {group.critical > 0 && <span className="text-rose-600"> · {criticalText(group.critical)}</span>}
                    </span>
                )}
            </div>
            <div className={`${iosCard} overflow-hidden`}>
                <div aria-hidden="true"
                     className={`hidden border-b border-slate-100 px-5 py-2 text-[11px] font-medium uppercase tracking-wide text-slate-400 ${DAY_COLUMNS}`}>
                    <span>День</span>
                    <span className="text-right" title="Средний балл ИИ за день">ИИ</span>
                    <span className="text-right" title="Средний балл человека за день">Человек</span>
                    <span className="text-right" title="Проверено человеком из оценённых ИИ">Проверено</span>
                    <span />
                </div>
                <div role="list">
                    {group.days.map((day) => (
                        <div role="listitem" key={day.day} className="border-b border-slate-100 last:border-b-0">
                            <DayRow day={day} onOpen={onOpenDay} />
                        </div>
                    ))}
                </div>
            </div>
        </section>
    );
}

/* Показатель сводки дня: подпись, крупное число, пояснение. Клетки разделены
 * волосяными линиями одной сеткой (gap-px на сером), а не рамками у каждой. */
function DayStat({ label, value, tone = null, sub = null, subTone = 'text-slate-500', wide = false, children = null }) {
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

function DaySummary({ day }) {
    const ai = round(day.ai_avg);
    const human = round(day.human_avg);
    const reviewed = day.reviewed || 0;
    const evaluated = Math.max(day.evaluated || 0, day.open || 0);
    const share = evaluated ? Math.min(1, reviewed / evaluated) : 0;
    return (
        <div className={`${iosCard} overflow-hidden`}>
            <dl className="grid grid-cols-2 gap-px bg-slate-100 sm:grid-cols-5">
                <DayStat wide label="Ждут проверки" value={day.open}
                         sub={day.critical ? criticalText(day.critical) : 'критических нет'}
                         subTone={day.critical ? 'font-medium text-rose-600' : 'text-slate-400'} />
                <DayStat label="Проверено" value={`${reviewed} из ${evaluated}`}>
                    <div className="mt-2.5 h-1 overflow-hidden rounded-full bg-slate-100" aria-hidden="true">
                        <div className="h-full rounded-full bg-emerald-500 transition-[width] duration-500"
                             style={{ width: `${Math.round(share * 100)}%` }} />
                    </div>
                </DayStat>
                <DayStat label="Поправили ИИ" value={reviewed ? day.corrected || 0 : null}
                         sub={reviewed ? `из ${reviewed} ${plural(reviewed, 'проверенного', 'проверенных', 'проверенных')}` : null} />
                <DayStat label="Средний балл ИИ" value={ai} tone={ai != null ? scoreTone(ai) : null}
                         sub={evaluated ? `${evaluated} ${plural(evaluated, 'разговор', 'разговора', 'разговоров')}` : null} />
                <DayStat label="Средний балл человека" value={human} tone={human != null ? scoreTone(human) : null}
                         sub={day.human_n ? `${day.human_n} ${plural(day.human_n, 'оценка', 'оценки', 'оценок')}` : null} />
            </dl>
        </div>
    );
}

/* Метки, которые стоят у каждого разговора дня, — одной тихой строкой над списком,
 * в том же виде, что в строке; что они значат — под «i» (у левого края: пузырёк
 * шириной 256 px раскрывается вправо и в конце строки уходил бы за экран). */
function CommonMarks({ keys }) {
    if (!keys.length) return null;
    const marks = [
        ...Object.keys(REASON).filter((key) => keys.includes(key)).map((key) => REASON[key]),
        ...(keys.includes(STALE_MARK) ? [STALE] : []),
    ];
    const text = ['Эти метки стоят у каждого разговора дня, поэтому в строках их нет.',
        ...marks.map((m) => `«${m.label}» — ${m.hint.charAt(0).toLowerCase()}${m.hint.slice(1)}.`)].join(' ');
    return (
        <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1 text-[12px] text-slate-500">
            <span className="flex items-center gap-1.5">
                У всех
                <IosHint text={text} label="Что значат метки у всех" />
            </span>
            {marks.map((m) => <QuietMark key={m.label} meta={m} />)}
        </div>
    );
}

function DayScreen({ days, day, state, reviewedKeys, onOpen, onOpenDay, onClose, onLoadMore, onRetry }) {
    const info = dayTitle(day.day);
    const items = (state?.items || []).filter((item) => !reviewedKeys.has(itemKey(item)));
    const common = commonReasons([day], items);
    const meta = commonRowMeta(items);
    const { earlier, later } = dayNeighbours(days, day.day);
    const next = nextDayToReview(days, day.day);
    // Сколько ещё не показано — по ответу самого дня (на момент запроса и докуда
    // дочитали), а не по сводке: её могли обогнать проверки.
    const remaining = state?.total != null ? Math.max(0, state.total - (state.end || 0)) : 0;
    const hasMore = Boolean(state) && !state.loading && !state.error && remaining > 0;
    // Проверено всё: по сводке (день закрыли здесь же) или по ответу дня (закрыли
    // другие). Пока закрытый день перечитывается, крутилка не нужна — итог известен.
    const done = !state?.error && !items.length && !hasMore
        && (day.open === 0 || (Boolean(state) && !state.loading));
    const loading = (!state || state.loading) && !done;
    const arrow = 'grid h-8 w-8 place-items-center rounded-lg text-slate-600 transition hover:bg-white hover:shadow-sm disabled:cursor-default disabled:text-slate-300 disabled:hover:bg-transparent disabled:hover:shadow-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60';

    return (
        <div className="space-y-4">
            <div className="flex items-center justify-between gap-2">
                <button type="button" onClick={onClose} className={`${iosBtnGhost} -ml-2`}>
                    <ChevronLeft size={16} aria-hidden="true" />Все дни
                </button>
                <div className="flex items-center gap-0.5 rounded-xl bg-slate-100 p-0.5">
                    <button type="button" className={arrow} disabled={!earlier} onClick={() => onOpenDay(earlier)}
                            aria-label={earlier ? `Предыдущий день: ${dayTitle(earlier).title}` : 'Предыдущего дня нет'}
                            title={earlier ? dayTitle(earlier).title : undefined}>
                        <ChevronLeft size={16} aria-hidden="true" />
                    </button>
                    <button type="button" className={arrow} disabled={!later} onClick={() => onOpenDay(later)}
                            aria-label={later ? `Следующий день: ${dayTitle(later).title}` : 'Следующего дня нет'}
                            title={later ? dayTitle(later).title : undefined}>
                        <ChevronRight size={16} aria-hidden="true" />
                    </button>
                </div>
            </div>

            <div className="px-1">
                <div className={`text-[13px] font-medium ${info.relative ? 'text-blue-600' : 'text-slate-500'}`}>
                    {info.relative ? `${info.relative}, ${info.weekday.toLowerCase()}` : info.weekday || ' '}
                </div>
                {/* Фокус сюда ставит CallQaView: день открыли из списка, или в нём
                    не осталось разговоров после проверки. */}
                <h2 id="qa-queue-day-title" tabIndex={-1}
                    className="rounded-md text-[24px] font-semibold leading-tight text-slate-900 outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60">
                    {info.date}
                </h2>
            </div>

            <DaySummary day={day} />

            <section aria-label="Разговоры дня" className="space-y-2">
                <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1 px-1">
                    <span className={iosGroupLabel.replace('px-1 ', '')}>Разговоры дня</span>
                    <CommonMarks keys={common} />
                </div>
                <div className={`${iosCard} overflow-hidden`}>
                    {items.length > 0 && <QueueList items={items} onOpen={onOpen} common={common} meta={meta} />}
                    {loading && (
                        <div className={`flex items-center justify-center gap-2 py-8 text-[13px] text-slate-500 ${items.length ? 'border-t border-slate-100' : ''}`}>
                            <Loader2 size={15} className="animate-spin" aria-hidden="true" />Загружаю разговоры…
                        </div>
                    )}
                    {state?.error && (
                        <div className="flex items-center justify-center gap-2 border-t border-slate-100 px-4 py-4 text-[13px] text-rose-600 first:border-t-0">
                            Не удалось загрузить разговоры дня
                            <button type="button" onClick={onRetry}
                                    className="rounded-lg px-2 py-1.5 font-medium text-blue-600 hover:bg-blue-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60">
                                Повторить
                            </button>
                        </div>
                    )}
                    {hasMore && (
                        <button type="button" onClick={onLoadMore}
                                className="w-full border-t border-slate-100 py-3 text-[13px] font-medium text-blue-600 transition hover:bg-slate-50 focus-visible:bg-blue-50/60 focus-visible:outline-none">
                            Показать ещё {remaining}
                        </button>
                    )}
                    {done && !state?.error && (
                        <div className="flex flex-col items-center gap-3 px-6 py-10 text-center">
                            <CheckCircle2 size={28} className="text-emerald-500" aria-hidden="true" />
                            <p className="text-[14px] font-semibold text-slate-800">Все разговоры дня проверены</p>
                            <div className="flex flex-wrap justify-center gap-2">
                                {next && (
                                    <button type="button" onClick={() => onOpenDay(next, { focusTitle: true })} className={iosBtnPrimary}>
                                        Следующий день — {dayTitle(next).date}<ChevronRight size={16} aria-hidden="true" />
                                    </button>
                                )}
                                <button type="button" onClick={onClose} className={iosBtnSecondary}>Все дни</button>
                            </div>
                        </div>
                    )}
                </div>
            </section>
        </div>
    );
}

export default function QueueDays({
    days, total = 0, truncated = false, openDay = null, onOpenDay, onCloseDay,
    dayItems, reviewedKeys, onOpen, onLoadMore, onRetryDay, onRefresh,
}) {
    const day = openDay ? days.find((item) => item.day === openDay) : null;
    if (day) {
        return (
            <DayScreen days={days} day={day} state={dayItems[day.day]} reviewedKeys={reviewedKeys}
                       onOpen={onOpen} onOpenDay={onOpenDay} onClose={onCloseDay}
                       onLoadMore={() => onLoadMore(day.day)} onRetry={() => onRetryDay(day.day)} />
        );
    }
    return <Overview days={days} total={total} truncated={truncated} onOpenDay={onOpenDay} onRefresh={onRefresh} />;
}
