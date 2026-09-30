import React, { useState } from 'react';
import { motion } from 'framer-motion';
import { ChevronDown, CheckCircle2, Loader2, RotateCcw, Users } from 'lucide-react';
import { iosCard, scoreTone } from '../ui/ios';
import QueueList, { REASON } from './QueueList';
import { commonReasons, dayTitle, itemKey, plural, queueSummary } from './queueDayRules';

/* Очередь ревью по дням разговора.
 *
 * Вместо одного длинного списка — карточка на день. Сверху карточки всегда видна
 * сводка дня: сколько ждёт проверки (и сколько из них критических), сколько
 * разговоров дня уже проверено, средние баллы ИИ и человека, почему разговоры
 * попали в очередь. Сами разговоры раскрываются по нажатию на заголовок; самый
 * свежий день открыт сразу. Данные и состояние живут в CallQaView: из карточки
 * разговора человек возвращается к тому же раскрытому дню.
 */

const DAYS_PAGE = 14;   // старые дни — по кнопке: сводка каждого и так на виду

const TONE_TEXT = { green: 'text-emerald-700', amber: 'text-amber-700', red: 'text-rose-600', slate: 'text-slate-400' };

/* Плитка сводки — как виджет в «Настройках»/«Экранном времени»: подпись, крупное
 * число, строка пояснения. Цвет — только у числа, где он что-то значит (балл).
 * На телефоне четыре плитки идут одной плотной строкой: подпись короче (`short`),
 * пояснение — только если оно важно сейчас (`subOnPhone`), иначе карточка дня
 * занимала бы полэкрана. */
function Stat({ label, short, value, tone = null, sub = null, subCls = 'text-slate-400', subOnPhone = false,
                progress = null, title }) {
    return (
        <div className="min-w-0 rounded-xl bg-slate-50 px-2 py-2 ring-1 ring-slate-100 sm:px-3 sm:py-2.5" title={title}>
            <div className="truncate text-[10.5px] font-medium text-slate-500 sm:text-[11px]">
                <span className="sm:hidden">{short || label}</span>
                <span className="hidden sm:inline">{label}</span>
            </div>
            <div className={`mt-0.5 truncate text-[15px] font-semibold leading-tight tabular-nums sm:text-[18px] ${tone ? TONE_TEXT[tone] : 'text-slate-900'}`}>
                {value}
            </div>
            {progress != null && (
                <div className="mt-1.5 h-1 overflow-hidden rounded-full bg-slate-200" aria-hidden="true">
                    <div className="h-full rounded-full bg-emerald-500 transition-[width] duration-500"
                         style={{ width: `${Math.round(Math.max(0, Math.min(1, progress)) * 100)}%` }} />
                </div>
            )}
            {sub && <div className={`mt-1 truncate text-[11px] ${subCls} ${subOnPhone ? '' : 'hidden sm:block'}`}>{sub}</div>}
        </div>
    );
}

function DayStats({ day }) {
    const score = day.ai_avg != null ? Math.round(day.ai_avg) : null;
    const human = day.human_avg != null ? Math.round(day.human_avg) : null;
    const done = day.open === 0;
    return (
        <div className="grid grid-cols-4 gap-1.5 sm:gap-2">
            <Stat label="Ждут проверки" short="Ждут" value={day.open}
                  sub={done ? 'всё проверено'
                      : day.critical ? `${day.critical} ${plural(day.critical, 'критический', 'критических', 'критических')}`
                          : 'без критических'}
                  subCls={done ? 'text-emerald-600' : day.critical ? 'font-medium text-rose-600' : 'text-slate-400'}
                  subOnPhone={Boolean(day.critical) && !done}
                  title="Разговоры дня, которые ИИ оценил, а человек ещё не проверил" />
            <Stat label="Проверено" value={`${day.reviewed} из ${day.evaluated}`}
                  progress={day.evaluated ? day.reviewed / day.evaluated : 0}
                  sub={day.corrected ? `с исправлениями ИИ: ${day.corrected}` : null}
                  title="Сколько разговоров дня, оценённых ИИ, уже проверил человек" />
            <Stat label="Балл ИИ" short="ИИ" value={score ?? '—'} tone={score != null ? scoreTone(score) : null}
                  sub={day.open > 1 && day.open_ai_min != null ? `мин. в очереди ${day.open_ai_min}` : 'в среднем за день'}
                  title="Средний балл ИИ по всем оценённым разговорам дня; «мин.» — самый низкий среди ждущих проверки" />
            <Stat label="Балл человека" short="Человек" value={human ?? '—'} tone={human != null ? scoreTone(human) : null}
                  sub={day.human_n ? `${day.human_n} ${plural(day.human_n, 'оценка', 'оценки', 'оценок')}` : 'оценок пока нет'}
                  title="Средний балл, который поставил человек (журнал или оценка в карточке)" />
        </div>
    );
}

/* Почему разговоры дня в очереди. Критические уже названы в плитке; причина,
 * которая есть у всех ждущих, подписана «у всех» и у строк не повторяется. */
function DayReasons({ day, common }) {
    const entries = Object.entries(day.reasons || {}).filter(([key, n]) => key !== 'critical' && n > 0);
    if (!entries.length || !day.open) return null;
    return (
        <div className="flex flex-wrap gap-1.5">
            {entries.map(([key, n]) => {
                const meta = REASON[key] || REASON.new;
                return (
                    <span key={key} title={meta.hint}
                          className="inline-flex items-center gap-1 rounded-full bg-slate-100 px-2 py-0.5 text-[11.5px] text-slate-600">
                        <meta.Icon size={11} className={TONE_TEXT[meta.tone] || 'text-slate-400'} aria-hidden="true" />
                        {meta.label}
                        <span className="tabular-nums text-slate-400">{common.includes(key) && day.open > 1 ? 'у всех' : n}</span>
                    </span>
                );
            })}
        </div>
    );
}

function DayCard({ day, open, onToggle, state, reviewedKeys, onOpen, onLoadMore, onRetry }) {
    const { title, relative } = dayTitle(day.day);
    const common = commonReasons(day);
    const items = (state?.items || []).filter((item) => !reviewedKeys.has(itemKey(item)));
    const directions = (day.directions || []).map((d) => d.name);
    const subtitle = [
        day.operators ? `${day.operators} ${plural(day.operators, 'сотрудник', 'сотрудника', 'сотрудников')}` : null,
        directions.length ? directions.slice(0, 2).join(', ') + (directions.length > 2 ? ` +${directions.length - 2}` : '') : null,
    ].filter(Boolean).join(' · ');
    // «Показать ещё» — только к уже показанным строкам: при сбое загрузки кнопка «Повторить».
    const hasMore = Boolean(state) && !state.loading && !state.error && items.length > 0 && items.length < day.open;
    const done = day.open === 0;
    const bodyId = `qa-day-${day.day}`;

    return (
        <section className={`${iosCard} overflow-hidden`} aria-label={title}>
            <button type="button" onClick={onToggle} aria-expanded={open} aria-controls={bodyId}
                    className="flex w-full items-start justify-between gap-3 px-4 pb-2 pt-3.5 text-left transition hover:bg-slate-50/70 focus-visible:bg-slate-50 focus-visible:outline-none">
                <div className="min-w-0">
                    <div className="flex flex-wrap items-baseline gap-x-2">
                        <h3 className="text-[15px] font-semibold text-slate-900">{title}</h3>
                        {relative && <span className="text-[12.5px] font-medium text-blue-600">{relative}</span>}
                    </div>
                    {subtitle && (
                        <p className="mt-0.5 flex items-center gap-1 truncate text-[12px] text-slate-500">
                            <Users size={12} className="shrink-0 text-slate-400" aria-hidden="true" />{subtitle}
                        </p>
                    )}
                </div>
                <div className="flex shrink-0 items-center gap-1.5 pt-0.5">
                    {done && <CheckCircle2 size={17} className="text-emerald-500" aria-label="Всё проверено" />}
                    <ChevronDown size={18} className={`text-slate-400 transition-transform duration-200 ${open ? 'rotate-180' : ''}`} aria-hidden="true" />
                </div>
            </button>

            <div className="space-y-2.5 px-4 pb-3.5">
                <DayStats day={day} />
                <DayReasons day={day} common={common} />
            </div>

            {open && !done && (
                <motion.div id={bodyId} initial={{ opacity: 0 }} animate={{ opacity: 1 }}
                            className="border-t border-slate-100">
                    {items.length > 0 && <QueueList items={items} onOpen={onOpen} common={common} />}
                    {state?.loading && (
                        <div className="flex items-center justify-center gap-2 py-4 text-[12.5px] text-slate-400">
                            <Loader2 size={14} className="animate-spin" />Загружаю разговоры…
                        </div>
                    )}
                    {state?.error && (
                        <div className="flex items-center justify-center gap-2 py-4 text-[12.5px] text-rose-600">
                            Не удалось загрузить разговоры дня
                            <button type="button" onClick={onRetry}
                                    className="rounded-md px-1.5 font-medium text-blue-600 hover:bg-blue-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60">
                                Повторить
                            </button>
                        </div>
                    )}
                    {hasMore && (
                        <button type="button" onClick={onLoadMore}
                                className="w-full border-t border-slate-100 py-2.5 text-[12.5px] font-medium text-blue-600 transition hover:bg-slate-50 focus-visible:bg-slate-50 focus-visible:outline-none">
                            Показать ещё — {day.open - items.length}
                        </button>
                    )}
                    {items.some((c) => c.stale) && (
                        <p className="border-t border-slate-100 px-4 py-2 text-[11.5px] text-slate-400">
                            «Устарела» — после оценки изменилась конфигурация ИИ; откроется прежняя оценка,
                            пересчёт — кнопкой «Переоценить» в карточке.
                        </p>
                    )}
                </motion.div>
            )}
        </section>
    );
}

export default function QueueDays({
    days, total = 0, truncated = false, expanded, onToggle, dayItems, reviewedKeys, onOpen,
    onLoadMore, onRetryDay, onRefresh,
}) {
    // Вернулись из карточки разговора — раскрытый дальний день не прячем за «Ещё дни».
    const [shown, setShown] = useState(() => Math.max(DAYS_PAGE,
        days.reduce((last, day, index) => (expanded.has(day.day) ? index + 1 : last), 0)));
    const summary = queueSummary(days);
    const visible = days.slice(0, shown);
    return (
        <div className="space-y-3">
            <div className="flex items-center justify-between gap-2 px-1">
                <p className="text-[12.5px] text-slate-500">
                    {summary.open
                        ? <>Ждут проверки <b className="font-semibold text-slate-700">{summary.open}</b>
                            {' '}· {summary.days} {plural(summary.days, 'день', 'дня', 'дней')}
                            {summary.critical ? <> · <span className="font-medium text-rose-600">{summary.critical} {plural(summary.critical, 'критический', 'критических', 'критических')}</span></> : null}</>
                        : 'Все разговоры в очереди проверены'}
                    {truncated && <span className="text-slate-400"> · показаны первые {total}</span>}
                </p>
                <button type="button" onClick={onRefresh} title="Обновить очередь" aria-label="Обновить очередь"
                        className="grid h-8 w-8 place-items-center rounded-lg text-slate-400 transition hover:bg-slate-100 hover:text-slate-600 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60">
                    <RotateCcw size={15} />
                </button>
            </div>
            {visible.map((day) => (
                <DayCard key={day.day} day={day} open={expanded.has(day.day)} onToggle={() => onToggle(day.day)}
                         state={dayItems[day.day]} reviewedKeys={reviewedKeys} onOpen={onOpen}
                         onLoadMore={() => onLoadMore(day.day)} onRetry={() => onRetryDay(day.day)} />
            ))}
            {days.length > shown && (
                <div className="flex justify-center pt-1">
                    <button type="button" onClick={() => setShown((n) => n + DAYS_PAGE)}
                            className="rounded-xl bg-slate-100 px-4 py-2 text-[13px] font-medium text-slate-600 transition hover:bg-slate-200 active:scale-[0.98] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60">
                        Ещё дни — {days.length - shown}
                    </button>
                </div>
            )}
        </div>
    );
}
